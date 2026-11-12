from datetime import date
from typing import Any, Literal

from pydantic import Field

from services.database_api.app.schemas.common import APIModel


class ModelIn(APIModel):
    model_name: str
    model_version: str
    task_type: Literal["clustering", "regression", "classification"]
    algorithm: str
    artifact_gcs_uri: str = Field(pattern=r"^gs://")
    scaler_gcs_uri: str | None = None
    feature_names: list[str] = Field(default_factory=list)
    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    evaluation_metrics: dict[str, Any] = Field(default_factory=dict)
    status: Literal["draft", "ready", "retired"] = "draft"
    train_start_hst: date | None = None
    train_end_hst: date | None = None
    test_start_hst: date | None = None
    test_end_hst: date | None = None
    artifact_generation: str | None = Field(default=None, pattern=r"^[0-9]+$")
    artifact_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    artifact_size_bytes: int | None = Field(default=None, ge=0)
    preprocessor_gcs_uri: str | None = None
    preprocessor_generation: str | None = None
    requirements_gcs_uri: str | None = None
    model_card_gcs_uri: str | None = None
    feature_schema_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    python_version: str | None = None


class DatasetSplitIn(APIModel):
    dataset_name: str
    dataset_version: str
    station_id: Literal["51201"] = "51201"
    task_type: Literal["clustering", "regression", "classification"]
    target_metric: str | None = None
    target_horizon_days: int | None = Field(default=None, gt=0)
    observation_granularity: Literal["day_hst", "hour_utc", "observation"] = "day_hst"
    feature_window_days: int | None = Field(default=None, gt=0)
    feature_columns: list[str] = Field(default_factory=list)
    label_definition: dict[str, Any] = Field(default_factory=dict)
    preprocessing_parameters: dict[str, Any] = Field(default_factory=dict)
    split_method: Literal["chronological", "walk_forward", "expanding_window", "other"] = "chronological"
    train_start_hst: date
    train_end_hst: date
    validation_start_hst: date | None = None
    validation_end_hst: date | None = None
    test_start_hst: date | None = None
    test_end_hst: date | None = None
    train_sample_count: int | None = Field(default=None, ge=0)
    validation_sample_count: int | None = Field(default=None, ge=0)
    test_sample_count: int | None = Field(default=None, ge=0)
    dataset_gcs_uri: str | None = None
    dataset_gcs_generation: str | None = None
    dataset_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    code_commit_sha: str | None = None


class TrainingRunIn(APIModel):
    dataset_split_id: int = Field(gt=0)
    model_id: int | None = None
    task_type: Literal["clustering", "regression", "classification"]
    algorithm: str
    run_name: str | None = None
    status: Literal["running", "success", "partial", "failed", "cancelled"] = "running"
    random_seed: int | None = None
    hyperparameters: dict[str, Any] = Field(default_factory=dict)


class EvaluationIn(APIModel):
    training_run_id: int = Field(gt=0)
    model_id: int | None = None
    dataset_split_id: int = Field(gt=0)
    evaluation_stage: Literal["train", "validation", "test", "walk_forward", "backtest", "live"]
    task_type: Literal["clustering", "regression", "classification"]
    evaluation_name: str = "default"
    sample_count: int = Field(ge=0)
    mae: float | None = Field(default=None, ge=0)
    mse: float | None = Field(default=None, ge=0)
    rmse: float | None = Field(default=None, ge=0)
    r2_score: float | None = None
    accuracy: float | None = Field(default=None, ge=0, le=1)
    precision_macro: float | None = Field(default=None, ge=0, le=1)
    recall_macro: float | None = Field(default=None, ge=0, le=1)
    f1_macro: float | None = Field(default=None, ge=0, le=1)
    silhouette_score: float | None = Field(default=None, ge=-1, le=1)
    davies_bouldin_score: float | None = Field(default=None, ge=0)
    calinski_harabasz_score: float | None = Field(default=None, ge=0)
    additional_metrics: dict[str, Any] = Field(default_factory=dict)


class EvaluationSampleIn(APIModel):
    evaluation_id: int = Field(gt=0)
    station_id: Literal["51201"] = "51201"
    target_date_hst: date
    target_name: str
    horizon_days: int = Field(default=1, gt=0)
    actual_value: float | None = None
    predicted_value: float | None = None
    actual_label: str | None = None
    predicted_label: str | None = None
    prediction_probabilities: dict[str, float] = Field(default_factory=dict)
    feature_cutoff_date_hst: date | None = None
    feature_snapshot: dict[str, Any] = Field(default_factory=dict)


class ClusterCentroidIn(APIModel):
    model_id: int = Field(gt=0)
    cluster_label: int
    cluster_name: str | None = None
    centroid_features: dict[str, float]
    description: str | None = None
    sample_count: int | None = Field(default=None, ge=0)


class ClassificationSchemeIn(APIModel):
    scheme_name: str
    scheme_version: str
    target_metric: str
    label_rules: dict[str, Any]
    rationale: str
