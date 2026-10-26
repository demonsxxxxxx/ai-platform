ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER 0:0
COPY --chown=0:0 tests/support/run_lifecycle_executor.py /app/run_lifecycle_executor.py
COPY --chown=0:0 tests/support/run_lifecycle_faults.py /app/run_lifecycle_faults.py
COPY --chown=0:0 tests/support/run_lifecycle_docker_proxy.py /app/run_lifecycle_docker_proxy.py
COPY --chown=0:0 tests/support/run_lifecycle_identity.py /app/run_lifecycle_identity.py
RUN chmod 0755 /app/run_lifecycle_executor.py /app/run_lifecycle_faults.py /app/run_lifecycle_docker_proxy.py /app/run_lifecycle_identity.py

USER 10001:10001
ENTRYPOINT ["python", "/app/run_lifecycle_executor.py"]
CMD ["uvicorn"]
