def notify(title: str, message: str) -> None:
    try:
        from winotify import Notification
        Notification(app_id="Voice Calendar", title=title,
                    msg=message, duration="long").show()
    except Exception:
        # Fallback so a reminder is never silently lost
        print(f"\a[REMINDER] {title}: {message}")