# Benchmark image for the TPC-DS 1 TB suite.
#
# It carries three things the stock Spark image does not have: the dsdgen data generator,
# the Hudi and Iceberg runtimes, and the S3A connector. The benchmark scripts themselves are
# mounted from a ConfigMap, so editing a script does not mean rebuilding the image.
#
# This image runs the OSS Apache Spark side of the comparison, plus the shared datagen and
# load phases. The Quanton side runs on the engine image the operator is configured with, so
# the jars it needs are supplied through spark.jars or spark.jars.packages instead.
#
# Build for the architecture of your cluster nodes. Graviton and other arm64 node groups
# need an arm64 image:
#   docker buildx build --platform linux/arm64 -t <registry>/tpcds-bench:1.0 --push .
ARG SPARK_VERSION=3.5.2
FROM apache/spark:${SPARK_VERSION}

ARG HUDI_VERSION=0.15.0
ARG ICEBERG_VERSION=1.6.1
ARG HADOOP_VERSION=3.3.4
ARG AWS_SDK_VERSION=1.12.262
ARG TPCDS_KIT_REPO=https://github.com/gregrahn/tpcds-kit.git

USER root

# dsdgen, built from the TPC-DS kit.
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc make flex bison git ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 ${TPCDS_KIT_REPO} /opt/tpcds-kit && \
    cd /opt/tpcds-kit/tools && \
    make OS=LINUX && \
    chmod +x dsdgen && \
    chmod -R a+rX /opt/tpcds-kit

# Lakehouse and object-storage jars. They go into Spark's own jars directory so no
# extraClassPath setting is needed on the OSS side.
ENV MAVEN=https://repo1.maven.org/maven2
RUN set -eux; \
    cd /opt/spark/jars; \
    curl -fsSLO ${MAVEN}/org/apache/hudi/hudi-spark3.5-bundle_2.12/${HUDI_VERSION}/hudi-spark3.5-bundle_2.12-${HUDI_VERSION}.jar; \
    curl -fsSLO ${MAVEN}/org/apache/iceberg/iceberg-spark-runtime-3.5_2.12/${ICEBERG_VERSION}/iceberg-spark-runtime-3.5_2.12-${ICEBERG_VERSION}.jar; \
    curl -fsSLO ${MAVEN}/org/apache/iceberg/iceberg-aws-bundle/${ICEBERG_VERSION}/iceberg-aws-bundle-${ICEBERG_VERSION}.jar; \
    curl -fsSLO ${MAVEN}/org/apache/hadoop/hadoop-aws/${HADOOP_VERSION}/hadoop-aws-${HADOOP_VERSION}.jar; \
    curl -fsSLO ${MAVEN}/com/amazonaws/aws-java-sdk-bundle/${AWS_SDK_VERSION}/aws-java-sdk-bundle-${AWS_SDK_VERSION}.jar; \
    chmod a+r /opt/spark/jars/*.jar

# Google Cloud Storage users: add the GCS connector here as well, for example
#   curl -fsSLO https://storage.googleapis.com/hadoop-lib/gcs/gcs-connector-hadoop3-latest.jar

ENV PATH="/opt/tpcds-kit/tools:${PATH}"

USER spark
