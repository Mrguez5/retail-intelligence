"""Deploy the Databricks version of the pipeline as a job and run it.

    .venv-databricks/Scripts/python databricks/deploy.py pipeline   # bronze -> dbt build -> dashboard refresh
    .venv-databricks/Scripts/python databricks/deploy.py bronze     # only the bronze task
    .venv-databricks/Scripts/python databricks/deploy.py pipeline --no-run

What it sets up:
- the code (pipeline/ readers, databricks/bronze_spark.py and the dbt project) is synced to the
  Unity Catalog Volume /Volumes/<catalog>/bronze/code; only new or changed files are uploaded;
- the notebook databricks/job_task.py is imported to /Workspace/Users/<you>/retail-intelligence;
  every task of the job runs it with a different `step` (bronze, dbt);
- the dashboard created by databricks/dashboard.py is refreshed after a successful build.

Why a Volume and a notebook: on Databricks Free Edition, reading many workspace files from a job
(a dbt project, or a Git checkout, which lives on the same file system) fails with
"OSError [Errno 5] Input/output error" (rate limited); Volume reads and notebooks are not affected.

Connection from the environment (DATABRICKS_HOST, DATABRICKS_TOKEN; DATABRICKS_HTTP_PATH names the
SQL warehouse the dbt and dashboard tasks use). The job itself stores no secret: the dbt step
authenticates as the job's own identity. Runs on serverless compute.
"""
from __future__ import annotations

import argparse
import io
import os
import time
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import catalog, jobs, workspace

ROOT = Path(__file__).resolve().parent.parent
CATALOG = os.environ.get("RI_DATABRICKS_CATALOG", "workspace")
CODE = ["pipeline/__init__.py", "pipeline/readers.py", "pipeline/pdf_invoices.py", "databricks/bronze_spark.py"]
DBT_SKIP = {"target", "logs", "dbt_packages", ".user.yml"}
DASHBOARD_NAME = "Retail Price & Procurement Intelligence"
JOB_NAMES = {"bronze": "retail-intelligence bronze", "pipeline": "retail-intelligence pipeline"}
RETRIES = dict(timeout_seconds=3600, max_retries=1, min_retry_interval_millis=30000)


def sync_code(w: WorkspaceClient) -> str:
    """Copy new or changed code files to the Volume /Volumes/<catalog>/bronze/code."""
    try:
        w.volumes.read(f"{CATALOG}.bronze.code")
    except Exception:
        w.volumes.create(CATALOG, "bronze", "code", catalog.VolumeType.MANAGED)
    volume = f"/Volumes/{CATALOG}/bronze/code"
    dbt_files = [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "dbt").rglob("*"))
                 if p.is_file() and not (set(p.relative_to(ROOT / "dbt").parts) & DBT_SKIP)]
    changed = 0
    for rel in CODE + dbt_files:
        target, content = f"{volume}/{rel}", (ROOT / rel).read_bytes()
        try:
            same = w.files.download(target).contents.read() == content
        except Exception:
            same = False
        if not same:
            w.files.upload(target, io.BytesIO(content), overwrite=True)
            changed += 1
    print(f"code in {volume}: {len(CODE) + len(dbt_files)} files, {changed} uploaded (new or changed)")
    return volume


def import_notebook(w: WorkspaceClient) -> str:
    folder = f"/Users/{w.current_user.me().user_name}/retail-intelligence"
    w.workspace.mkdirs(folder)
    path = f"{folder}/job_task"
    w.workspace.import_(path, content=__import__("base64").b64encode((ROOT / "databricks/job_task.py").read_bytes()).decode(),
                        format=workspace.ImportFormat.SOURCE, language=workspace.Language.PYTHON, overwrite=True)
    return path


def upsert_job(w: WorkspaceClient, what: str) -> int:
    name = JOB_NAMES[what]
    sync_code(w)
    notebook = import_notebook(w)
    warehouse_id = os.environ["DATABRICKS_HTTP_PATH"].rstrip("/").rsplit("/", 1)[-1]

    def step(key, description, params, depends=None):
        return jobs.Task(task_key=key, description=description,
                         depends_on=[jobs.TaskDependency(task_key=d) for d in (depends or [])],
                         notebook_task=jobs.NotebookTask(notebook_path=notebook, source=jobs.Source.WORKSPACE,
                                                         base_parameters={"catalog": CATALOG, **params}),
                         **RETRIES)

    tasks = [step("bronze", "Land the Volume's raw files in Delta tables (incremental, with lineage)",
                  {"step": "bronze"})]
    if what == "pipeline":
        tasks.append(step("dbt_build", "Silver and gold models plus every data test (dbt build)",
                          {"step": "dbt", "warehouse_id": warehouse_id}, ["bronze"]))
        dashboard = next((x for x in w.lakeview.list() if x.display_name == DASHBOARD_NAME), None)
        if dashboard:      # created by databricks/dashboard.py; refreshed after every successful build
            tasks.append(jobs.Task(
                task_key="refresh_dashboard",
                description="Refresh the AI/BI dashboard on the new gold data",
                depends_on=[jobs.TaskDependency(task_key="dbt_build")],
                dashboard_task=jobs.DashboardTask(dashboard_id=dashboard.dashboard_id, warehouse_id=warehouse_id),
            ))
    settings = dict(name=name, tasks=tasks, max_concurrent_runs=1)
    existing = list(w.jobs.list(name=name))
    if existing:
        job_id = existing[0].job_id
        w.jobs.reset(job_id=job_id, new_settings=jobs.JobSettings(**settings))
        print(f"job updated: {name} ({job_id})")
    else:
        job_id = w.jobs.create(**settings).job_id
        print(f"job created: {name} ({job_id})")
    return job_id


def run(w: WorkspaceClient, job_id: int) -> None:
    run_id = w.jobs.run_now(job_id=job_id).run_id
    print(f"run started: {run_id}")
    while True:
        r = w.jobs.get_run(run_id)
        life = r.state.life_cycle_state.value if r.state and r.state.life_cycle_state else "?"
        if life in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
            break
        time.sleep(15)
    result = r.state.result_state.value if r.state.result_state else life
    secs = ((r.end_time or 0) - (r.start_time or 0)) / 1000
    print(f"run {run_id}: {result} in {secs:.0f}s")
    for t in r.tasks or []:
        state = t.state.result_state.value if t.state and t.state.result_state else "?"
        secs = ((t.end_time or 0) - (t.start_time or 0)) / 1000
        print(f"--- task {t.task_key}: {state} ({secs:.0f}s)")
        if state not in ("SUCCESS", "?") and t.task_key != "refresh_dashboard":
            out = w.jobs.get_run_output(t.run_id)
            print("ERROR:", out.error)
            print((out.error_trace or "")[-3000:])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=list(JOB_NAMES))
    ap.add_argument("--no-run", action="store_true")
    args = ap.parse_args()
    w = WorkspaceClient()
    job_id = upsert_job(w, args.what)
    if not args.no_run:
        run(w, job_id)


if __name__ == "__main__":
    main()
