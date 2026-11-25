"""Deploy the Databricks version of the pipeline to your workspace and run it.

    .venv-databricks/Scripts/python databricks/deploy.py pipeline   # bronze -> dbt build (silver, gold, tests)
    .venv-databricks/Scripts/python databricks/deploy.py bronze     # only the bronze task
    .venv-databricks/Scripts/python databricks/deploy.py pipeline --no-run

Connection from the environment (DATABRICKS_HOST, DATABRICKS_TOKEN; DATABRICKS_HTTP_PATH names the
SQL warehouse the dbt task runs on). The code and the dbt project are copied to
/Workspace/Users/<you>/retail-intelligence and run on serverless compute; Python dependencies
are declared in the job's environment, so nothing is installed by hand.
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import compute, jobs, workspace

ROOT = Path(__file__).resolve().parent.parent
CATALOG = os.environ.get("RI_DATABRICKS_CATALOG", "workspace")
CODE = ["pipeline/__init__.py", "pipeline/readers.py", "pipeline/pdf_invoices.py", "databricks/bronze_spark.py"]
DEPENDENCIES = ["pdfplumber>=0.11", "openpyxl>=3.1"]
DBT_DEPENDENCIES = ["dbt-databricks>=1.9,<2.0"]
DBT_SKIP = {"target", "logs", "dbt_packages", "profiles.yml", ".user.yml"}
JOB_NAMES = {"bronze": "retail-intelligence bronze", "pipeline": "retail-intelligence pipeline"}


def upload_code(w: WorkspaceClient) -> str:
    me = w.current_user.me().user_name
    base = f"/Workspace/Users/{me}/retail-intelligence"
    dbt_files = [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "dbt").rglob("*"))
                 if p.is_file() and not (set(p.relative_to(ROOT / "dbt").parts) & DBT_SKIP)]
    for rel in CODE + dbt_files:
        target = f"{base}/{rel}"
        w.workspace.mkdirs(target.rsplit("/", 1)[0])
        w.workspace.upload(target, (ROOT / rel).read_bytes(), format=workspace.ImportFormat.AUTO, overwrite=True)
    print(f"code uploaded to {base} ({len(CODE)} files + dbt project: {len(dbt_files)} files)")
    time.sleep(10)   # workspace files can take a moment to be readable by a job that starts at once
    return base


def upsert_job(w: WorkspaceClient, base: str, what: str) -> int:
    name = JOB_NAMES[what]
    envs = [jobs.JobEnvironment(environment_key="default",
                                spec=compute.Environment(environment_version="2", dependencies=DEPENDENCIES))]
    tasks = [jobs.Task(
        task_key="bronze",
        description="Land the Volume's raw files in Delta tables (incremental, with lineage)",
        environment_key="default",
        spark_python_task=jobs.SparkPythonTask(python_file=f"{base}/databricks/bronze_spark.py",
                                               parameters=[base, CATALOG], source=jobs.Source.WORKSPACE),
        timeout_seconds=3600,
    )]
    if what == "pipeline":
        warehouse_id = os.environ["DATABRICKS_HTTP_PATH"].rstrip("/").rsplit("/", 1)[-1]
        envs.append(jobs.JobEnvironment(environment_key="dbt",
                                        spec=compute.Environment(environment_version="2", dependencies=DBT_DEPENDENCIES)))
        tasks.append(jobs.Task(
            task_key="dbt_build",
            description="Silver and gold models plus every data test (dbt build)",
            depends_on=[jobs.TaskDependency(task_key="bronze")],
            environment_key="dbt",
            dbt_task=jobs.DbtTask(
                project_directory=f"{base}/dbt", source=jobs.Source.WORKSPACE,
                warehouse_id=warehouse_id, catalog=CATALOG, schema="retail_intelligence",
                commands=["dbt debug",
                          f"dbt build --vars '{{raw_dir: /Volumes/{CATALOG}/bronze/landing}}'"]),
            timeout_seconds=3600,
        ))
    settings = dict(name=name, environments=envs, tasks=tasks, max_concurrent_runs=1)
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
    print(f"run {run_id}: {result} in {secs:.0f}s - {r.run_page_url}")
    for t in r.tasks or []:
        print(f"--- task {t.task_key}: {t.state.result_state.value if t.state and t.state.result_state else '?'}")
        out = w.jobs.get_run_output(t.run_id)
        if out.logs:
            print(out.logs[-4000:])
        if out.error and result != "SUCCESS":
            print("ERROR:", out.error)
            print((out.error_trace or "")[-3000:])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=list(JOB_NAMES))
    ap.add_argument("--no-run", action="store_true")
    args = ap.parse_args()
    w = WorkspaceClient()
    base = upload_code(w)
    job_id = upsert_job(w, base, args.what)
    if not args.no_run:
        run(w, job_id)


if __name__ == "__main__":
    main()
