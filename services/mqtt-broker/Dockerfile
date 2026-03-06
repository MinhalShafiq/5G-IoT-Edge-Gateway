# =============================================================================
# Dockerfile — Mosquitto MQTT Broker
# 5G IoT Edge Gateway Project
#
# Base image: eclipse-mosquitto:2 (Alpine-based, ~12 MB)
# =============================================================================

FROM eclipse-mosquitto:2

# Copy configuration files into the container.
COPY mosquitto.conf /mosquitto/config/mosquitto.conf
COPY acl.conf       /mosquitto/config/acl.conf

# Ensure the persistence directory exists and is writable by the mosquitto
# user (UID 1883 in the official image).
RUN mkdir -p /mosquitto/data /mosquitto/log && \
    chown -R mosquitto:mosquitto /mosquitto/data /mosquitto/log

# Expose the MQTT listener port.
EXPOSE 1883

# The base image already sets the entrypoint to mosquitto; we just need to
# point it at our configuration file.
CMD ["mosquitto", "-c", "/mosquitto/config/mosquitto.conf"]
