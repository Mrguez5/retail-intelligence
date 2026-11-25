"""Deploy the Databricks version of the pipeline to your workspace and run it.

    .venv-databricks/Scripts/python databricks/deploy.py bronze     # upload code, (re)create the job, run it
    .venv-databricks/Scripts/python databricks/deploy.py bronze --no-run

Connection from the environment (DATABRICKS_HOST, DATABRICKS_TOKEN). The code is copied to
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
JOB_NAME = "retail-intelligence bronze"


def upload_code(w: WorkspaceClient) -> str:
    me = w.current_user.me().user_name
    base = f"/Workspace/Users/{me}/retail-intelligence"
    for rel in CODE:
        target = f"{base}/{rel}"
        w.workspace.mkdirs(target.rsplit("/", 1)[0])
        w.workspace.upload(target, (ROOT / rel).read_bytes(), format=workspace.ImportFormat.AUTO, overwrite=True)
    print(f"code uploaded to {base} ({len(CODE)} files)")
    time.sleep(10)   # workspace files can take a moment to be readable by a job that starts at once
    return base


def upsert_job(w: WorkspaceClient, base: str) -> int:
    settings = dict(
        name=JOB_NAME,
        environments=[jobs.JobEnvironment(environment_key="default",
                                          spec=compute.Environment(environment_version="2", dependencies=DEPENDENCIES))],
        tasks=[jobs.Task(
            task_key="bronze",
            description="Land the Volume's raw files in Delta tables (incremental, with lineage)",
            environment_key="default",
            spark_python_task=jobs.SparkPythonTask(python_file=f"{base}/databricks/bronze_spark.py",
                                                   parameters=[base, CATALOG], source=jobs.Source.WORKSPACE),
            timeout_seconds=3600,
        )],
        max_concurrent_runs=1,
    )
    existing = [j for j in w.jobs.list(name=JOB_NAME)]
    if existing:
        job_id = existing[0].job_id
        w.jobs.reset(job_id=job_id, new_settings=jobs.JobSettings(**settings))
        print(f"job updated: {JOB_NAME} ({job_id})")
    else:
        job_id = w.jobs.create(**settings).job_id
        print(f"job created: {JOB_NAME} ({job_id})")
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
        out = w.jobs.get_run_output(t.run_id)
        if out.logs:
            print(out.logs[-4000:])
        if out.error and result != "SUCCESS":
            print("ERROR:", out.error)
            print((out.error_trace or "")[-3000:])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["bronze"])
    ap.add_argument("--no-run", action="store_true")
    args = ap.parse_args()
    w = WorkspaceClient()
    base = upload_code(w)
    job_id = upsert_job(w, base)
    if not args.no_run:
        run(w, job_id)


if __name__ == "__main__":
    main()
