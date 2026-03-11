import re
import os

def sanitize_file_name(name: str) -> str:
    # Remove illegal characters for Windows/Linux filesystems
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    # Remove hidden characters and control characters
    name = "".join(char for char in name if char.isprintable())
    # Limit length
    if len(name) > 200:
        base, ext = os.path.splitext(name)
        name = base[:190] + ext
    return name.strip()

def format_bytes(size: int) -> str:
    power = 2**10
    n = 0
    power_labels = {0: '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"
