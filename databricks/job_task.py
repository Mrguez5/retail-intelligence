# Databricks notebook source
# MAGIC %md
# MAGIC # retail-intelligence job task
# MAGIC One notebook, two steps of the job (parameter `step`):
# MAGIC - `bronze`: land the Volume's raw files in Delta tables (`databricks/bronze_spark.py`);
# MAGIC - `dbt`: `dbt build` of the silver and gold models plus every data test, on the SQL warehouse.
# MAGIC
# MAGIC The code is read from the Volume `/Volumes/<catalog>/bronze/code`, where `databricks/deploy.py`
# MAGIC syncs it (Free Edition throttles workspace-file reads, Volume reads are reliable).

# COMMAND ----------

# MAGIC %pip install -q "pdfplumber>=0.11" "openpyxl>=3.1" "dbt-databricks>=1.9,<2.0"

# COMMAND ----------

import os
import shutil
import sys

dbutils.widgets.text("step", "bronze")
dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("warehouse_id", "")
dbutils.widgets.text("simulation", "true")   # "false": data that is not the full generated dataset
STEP = dbutils.widgets.get("step")
CATALOG = dbutils.widgets.get("catalog")
CODE = f"/Volumes/{CATALOG}/bronze/code"
WORK = "/tmp/retail-intelligence"

shutil.rmtree(WORK, ignore_errors=True)
shutil.copytree(CODE, WORK)          # a local copy: dbt writes target/ and logs/ next to the project
print(f"step {STEP}: code copied from {CODE}")

# COMMAND ----------

if STEP == "bronze":
    sys.argv = [f"{WORK}/databricks/bronze_spark.py", WORK, CATALOG]
    with open(sys.argv[0]) as fh:
        exec(compile(fh.read(), sys.argv[0], "exec"), {"__name__": "__main__"})

# COMMAND ----------

if STEP == "dbt":
    from databricks.sdk import WorkspaceClient
    from dbt.cli.main import dbtRunner

    cfg = WorkspaceClient().config                       # the job's own identity, no stored secret
    os.environ["DATABRICKS_HOST"] = cfg.host.replace("https://", "").rstrip("/")
    os.environ["DATABRICKS_TOKEN"] = cfg.authenticate()["Authorization"].split(" ", 1)[1]
    os.environ["DATABRICKS_HTTP_PATH"] = "/sql/1.0/warehouses/" + dbutils.widgets.get("warehouse_id")
    os.environ["RI_DATABRICKS_CATALOG"] = CATALOG
    project = f"{WORK}/dbt"
    common = ["--project-dir", project, "--profiles-dir", project, "--target", "databricks"]
    simulation = "false" if dbutils.widgets.get("simulation").lower() == "false" else "true"
    dbt_vars = f"{{raw_dir: /Volumes/{CATALOG}/bronze/landing, simulation: {simulation}}}"
    print(f"dbt vars: {dbt_vars}")
    for args in (["debug"], ["build", "--vars", dbt_vars]):
        result = dbtRunner().invoke(args[:1] + common + args[1:])
        if not result.success:
            raise RuntimeError(f"dbt {args[0]} failed: {result.exception or 'see the log above'}")
    print("dbt build: all models and tests passed")
