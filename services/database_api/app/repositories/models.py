from services.database_api.app.repositories.common import insert_row, upsert_row
from services.database_api.app.schemas.ml import ClassificationSchemeIn, ClusterCentroidIn, DatasetSplitIn, EvaluationIn, EvaluationSampleIn, ModelIn, TrainingRunIn


def save_model_metadata(conn, item: ModelIn):
    values = item.model_dump(exclude_none=True)
    result = insert_row(
        conn, "gold", "model_registry", values,
        conflict="ON CONFLICT (model_name, model_version) DO NOTHING",
        returning="model_id",
    ).fetchone()
    if result:
        return {"model_id": result["model_id"], "duplicate": False}
    result = conn.execute(
        "SELECT model_id FROM gold.model_registry WHERE model_name=%s AND model_version=%s",
        (item.model_name, item.model_version),
    ).fetchone()
    return {"model_id": result["model_id"], "duplicate": True}


def get_model_metadata(conn, model_id: int):
    return conn.execute("SELECT * FROM gold.model_registry WHERE model_id=%s", (model_id,)).fetchone()


def save_training_run(conn, item: TrainingRunIn):
    if item.run_name:
        previous = conn.execute("SELECT training_run_id FROM gold.training_runs WHERE run_name=%s ORDER BY training_run_id DESC LIMIT 1", (item.run_name,)).fetchone()
        if previous:
            return previous
    return insert_row(conn, "gold", "training_runs", item.model_dump(exclude_none=True), returning="training_run_id").fetchone()


def save_dataset_split(conn, item: DatasetSplitIn):
    return insert_row(conn, "gold", "dataset_splits", item.model_dump(exclude_none=True), conflict="ON CONFLICT (dataset_name, dataset_version) DO UPDATE SET dataset_name=EXCLUDED.dataset_name", returning="dataset_split_id").fetchone()


def save_evaluation(conn, item: EvaluationIn):
    return insert_row(conn, "gold", "model_evaluations", item.model_dump(exclude_none=True), conflict="ON CONFLICT (training_run_id, evaluation_stage, evaluation_name) DO UPDATE SET evaluation_name=EXCLUDED.evaluation_name", returning="evaluation_id").fetchone()


def save_evaluation_sample(conn, item: EvaluationSampleIn):
    return insert_row(conn, "gold", "evaluation_samples", item.model_dump(exclude_none=True), conflict="ON CONFLICT (evaluation_id, station_id, target_date_hst, target_name, horizon_days) DO NOTHING", returning="evaluation_sample_id").fetchone()


def save_cluster_centroid(conn, item: ClusterCentroidIn):
    values = item.model_dump(exclude_none=True)
    upsert_row(conn, "gold", "cluster_centroids", values, ("model_id", "cluster_label"))
    return {"model_id": item.model_id, "cluster_label": item.cluster_label}


def save_classification_scheme(conn, item: ClassificationSchemeIn):
    return insert_row(conn, "gold", "classification_schemes", item.model_dump(), conflict="ON CONFLICT (scheme_name, scheme_version) DO UPDATE SET scheme_name=EXCLUDED.scheme_name", returning="scheme_id").fetchone()
