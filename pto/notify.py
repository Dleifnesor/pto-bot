"""Windows toast notifications (falls back to the log if toasts are unavailable)."""
from . import config, log

_log = log.get("notify")


def toast(title: str, message: str, *, open_dashboard: bool = True) -> None:
    _log.info("NOTIFY %s: %s", title, message)
    cfg = config.load()
    if not cfg.notifications.windows_toast:
        return
    try:
        from winotify import Notification

        launch = f"http://{cfg.web.host}:{cfg.web.port}/" if open_dashboard else ""
        Notification(app_id="PTO Sticker Bot", title=title[:64], msg=message[:250], launch=launch).show()
    except Exception as e:  # noqa: BLE001 - notifications must never crash a job
        _log.warning("toast failed: %s", e)
