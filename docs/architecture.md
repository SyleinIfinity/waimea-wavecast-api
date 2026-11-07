# Waimea WaveCast backend topology

```mermaid
flowchart LR
    N[NOAA/CDIP/Open-Meteo] --> I[Ingestion]
    I --> GCS[(Cloud Storage Raw)]
    I --> API[Database API]
    API --> SQL[(Cloud SQL Bronze/Silver/Gold)]
    P[Preprocessing] --> API
    A[Analysis] --> API
    C[Clustering] --> API
    R[Prediction] --> API
    F[Classification] --> API
    B[Browser Frontend] -->|HTTPS| NX[VM Nginx]
    NX -->|localhost:8000| API
```

- CDIP 106 and NOAA 51201 share **one physical buoy**.
- Open-Meteo is **model** data; `issued_at` differs from `valid_at`. Do not mix with observations.
- Compute Engine VM service account has Cloud SQL Client and scoped GCS permissions.
- Cloud SQL Auth Proxy uses **private IP**. It does not create VPC connectivity; first set up Cloud SQL private network access.
- Nginx is a **host process**, TLS via Certbot. Docker Compose binds API only to loopback `127.0.0.1:8000`.
- Public access allowed to TCP 80/443 for the VM; SSH restricted through IAP. Do not open database port publicly.
- Store UTC timestamps in DB and compute calendar day as Pacific/Honolulu.
- `dbwaimea.liems.io.vn` optional private DNS; not exposed to Internet.
