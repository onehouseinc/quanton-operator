# Data Collection

1. The operator sends your entire QuantonSparkApplication yaml to the Onehouse control plane for ease of use and debugging. However, it makes sure that sensitive parameters are masked before sending. Checkout `Spark Parameter Masking` in [configurations](/docs/configurations.md) for more information and controls.

2. The operator collects operational metrics to monitor operator health. Metrics are collected using [OpenTelemetry](https://opentelemetry.io/) and forwarded to the Onehouse control plane. More information about this metrics is available [here](/docs/metrics.md)

3. The operator also collects resource usage metrics to know track how much CPU is used to run drivers/executors spawned by QuantonSparkApplications. More information about this metrics is available [here](/docs/metrics.md)
   
4. Scarf: The operator uses Scarf to collect anonymous usage data (pixel and package tracking) to better understand how users use the system, the website, and the docs and where to focus improvements next. Scarf fully supports the GDPR. The privacy policy of Scarf is available at https://about.scarf.sh/privacy-policy.
