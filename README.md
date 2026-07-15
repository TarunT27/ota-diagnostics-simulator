# OTA Update and Diagnostics Simulator

Small virtual-controller prototype for validating software versions, update checks, and rollback behavior.

## Included now

- Version comparison and checksum validation
- Successful and failed update states
- Rollback to the previous controller version
- Unit/integration-style tests and a minimal Docker image

This is an initial prototype. Later work will add multiple virtual controllers, update packages, and diagnostic event logs.

## Run

```bash
python main.py
python -m unittest discover -s tests -p "test_*.py"
```
