# Metrics and Telemetry

The Quanton Operator collects operational and usage metrics to provide observability into Spark job execution and operator health. Metrics are collected using [OpenTelemetry](https://opentelemetry.io/) and forwarded to the Onehouse control plane.

## What Is Collected
### Resource Usage Metrics

A [kube-state-metrics](https://github.com/kubernetes/kube-state-metrics) sidecar collects pod-level resource metrics from Spark driver and executor pods. These metrics are scoped to the namespaces configured in `quantonOperator.jobNamespaces` and include:

- Pod phase transitions
- CPU and memory requests and limits per container
- Pod lifecycle timestamps

These metrics are used for billing when if you use enterprise version.

## Controller Metrics Reference

The following sections document all Prometheus metrics exposed by the Quanton Operator controller on the `/metrics` endpoint (port `8080`) and can be used to configure your own alerts.

### Quanton Operator Metrics
Custom metrics specific to the Quanton Operator's business logic.

#### Reconciliation

| Metric | Type | Labels | Description |
|---|---|---|---|
| `quanton_active_reconciliations` | Gauge | — | Current number of active reconciliations in progress. |
| `quanton_reconciliations_total` | Counter | `status` | Total number of reconciliation attempts. Label values: `success`. |
| `quanton_reconciliation_duration_seconds` | Histogram | — | Time taken per reconciliation in seconds. |

#### QuantonSparkApplication Lifecycle

| Metric | Type | Labels | Description |
|---|---|---|---|
| `quanton_quantonspark_total` | Counter | — | Total number of QuantonSparkApplication CRs created. |
| `quanton_spark_application_submissions_total` | Counter | `status` | Total number of SparkApplication submission attempts. Label values: `success`, `failure`. |
| `quanton_spark_application_completion_total` | Counter | `phase` | Total number of SparkApplications completed. Label values: `COMPLETED`. |
| `quanton_spark_applications_by_phase` | Gauge | `phase` | Current count of SparkApplications by phase. Label values: `SUBMITTED`, `RUNNING`, `COMPLETED`, `UNKNOWN`. |

#### Latency

| Metric | Type | Labels | Description |
|---|---|---|---|
| `quanton_submission_latency_seconds` | Histogram | — | End-to-end time taken to submit a SparkApplication in seconds. Includes validation and control plane calls. |
| `quanton_spark_app_creation_duration_seconds` | Histogram | — | Time taken to create a SparkApplication resource in Kubernetes in seconds. |
| `quanton_validation_duration_seconds` | Histogram | — | Time taken to validate a QuantonSparkApplication spec in seconds. Includes control plane API calls for validation. |

#### Gateway Controller API

| Metric | Type | Labels | Description |
|---|---|---|---|
| `quanton_gateway_calls_total` | Counter | `result` | Total number of Control Plane API calls. Label values: `success`, `cache_hit`. |
| `quanton_gateway_call_duration_seconds` | Histogram | — | Time taken to call the Control Plane API in seconds. |

### Controller Runtime Metrics

Standard metrics from the [controller-runtime](https://github.com/kubernetes-sigs/controller-runtime) framework (kubebuilder) are also available.

### Work Queue Metrics

Metrics from the Kubernetes client-go work queue used by the controller.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `workqueue_adds_total` | Counter | `controller`, `name` | Total number of items added to the work queue. |
| `workqueue_depth` | Gauge | `controller`, `name`, `priority` | Current number of items in the work queue. |
| `workqueue_retries_total` | Counter | `controller`, `name` | Total number of retries handled by the work queue. |
| `workqueue_queue_duration_seconds` | Histogram | `controller`, `name` | Time an item stays in the work queue before being picked up for processing in seconds. |
| `workqueue_work_duration_seconds` | Histogram | `controller`, `name` | Time spent processing an item from the work queue in seconds. |
| `workqueue_longest_running_processor_seconds` | Gauge | `controller`, `name` | Duration the longest-running processor has been running in seconds. Large values indicate stuck threads. |
| `workqueue_unfinished_work_seconds` | Gauge | `controller`, `name` | Seconds of work in progress that has not been observed by `work_duration`. Large values indicate stuck threads. |

### REST Client Metrics

Metrics for Kubernetes API server requests made by the controller.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `rest_client_requests_total` | Counter | `code`, `host`, `method` | Total number of HTTP requests to the Kubernetes API server, partitioned by status code, method, and host. |

### Certificate Watcher Metrics

Metrics for TLS certificate management.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `certwatcher_read_certificate_total` | Counter | — | Total number of certificate reads. |
| `certwatcher_read_certificate_errors_total` | Counter | — | Total number of certificate read errors. |

### Leader Election Metrics

| Metric | Type | Labels | Description |
|---|---|---|---|
| `leader_election_master_status` | Gauge | `name` | Whether this instance is the leader for the given lease. `1` = leader, `0` = standby. |

### Process Metrics

Standard process-level metrics from the Prometheus client library.

| Metric | Type | Description |
|---|---|---|
| `process_cpu_seconds_total` | Counter | Total user and system CPU time spent in seconds. |
| `process_resident_memory_bytes` | Gauge | Resident memory size in bytes. |
| `process_virtual_memory_bytes` | Gauge | Virtual memory size in bytes. |
| `process_virtual_memory_max_bytes` | Gauge | Maximum amount of virtual memory available in bytes. |
| `process_open_fds` | Gauge | Number of open file descriptors. |
| `process_max_fds` | Gauge | Maximum number of open file descriptors. |
| `process_start_time_seconds` | Gauge | Start time of the process since Unix epoch in seconds. |
| `process_network_receive_bytes_total` | Counter | Number of bytes received by the process over the network. |
| `process_network_transmit_bytes_total` | Counter | Number of bytes sent by the process over the network. |

### Go Runtime Metrics

Standard Go runtime metrics are also exposed, including:

| Metric | Type | Description |
|---|---|---|
| `go_goroutines` | Gauge | Number of goroutines that currently exist. |
| `go_threads` | Gauge | Number of OS threads created. |
| `go_info` | Gauge | Go environment information (version label). |
| `go_memstats_alloc_bytes` | Gauge | Bytes allocated in heap and currently in use. |
| `go_memstats_alloc_bytes_total` | Counter | Total bytes allocated in heap (cumulative). |
| `go_memstats_heap_alloc_bytes` | Gauge | Heap bytes allocated and in use. |
| `go_memstats_heap_inuse_bytes` | Gauge | Heap bytes in use. |
| `go_memstats_heap_sys_bytes` | Gauge | Heap bytes obtained from system. |
| `go_memstats_sys_bytes` | Gauge | Total bytes obtained from system. |
| `go_memstats_stack_inuse_bytes` | Gauge | Stack bytes in use. |
| `go_gc_duration_seconds` | Summary | Wall-time pause duration in GC cycles. |
| `go_sched_goroutines_goroutines` | Gauge | Count of live goroutines. |
| `go_sched_gomaxprocs_threads` | Gauge | Current GOMAXPROCS setting. |

Additional detailed Go runtime metrics (`go_cpu_classes_*`, `go_gc_*`, `go_memory_classes_*`, `go_sched_*`, `go_godebug_*`) are also exposed for advanced debugging. These follow the standard Go runtime metrics conventions documented in the [Go runtime/metrics package](https://pkg.go.dev/runtime/metrics).

## Key Metrics for Monitoring

The following metrics are recommended for setting up alerts and dashboards:

| Use Case | Metric(s) |
|---|---|
| Reconciliation errors | `controller_runtime_reconcile_errors_total`, `quanton_reconciliations_total{status!="success"}` |
| Reconciliation latency | `quanton_reconciliation_duration_seconds`, `controller_runtime_reconcile_time_seconds` |
| Submission failures | `quanton_spark_application_submissions_total{status="failure"}` |
| Gateway API health | `quanton_gateway_calls_total`, `quanton_gateway_call_duration_seconds` |
| Work queue backup | `workqueue_depth`, `workqueue_unfinished_work_seconds` |
| Application phase tracking | `quanton_spark_applications_by_phase` |
| Leader election status | `leader_election_master_status` |
| Process health | `process_resident_memory_bytes`, `go_goroutines` |
