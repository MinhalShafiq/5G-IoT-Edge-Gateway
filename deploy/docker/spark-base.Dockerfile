FROM python:3.11-slim

# Install Java 17 runtime required by PySpark
RUN apt-get update && \
    apt-get install -y --no-install-recommends openjdk-17-jre-headless && \
    rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64

# Install PySpark
RUN pip install --no-cache-dir pyspark==3.5.0
