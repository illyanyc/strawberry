"""Entrypoint. Phase 0 placeholder: runs only the standard ops surface (healthz, readyz, metrics)."""

from tbcore.service import ServiceSettings, run


def main() -> None:
    """Start the service with settings from TB_* environment variables."""
    run(ServiceSettings(service="ml"))


if __name__ == "__main__":
    main()
