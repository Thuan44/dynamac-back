import base64
import logging
import plistlib
from typing import List, Tuple

logger = logging.getLogger(__name__)


def hour_to_decimal(hour: str) -> float:
    """
    Convert hour in HH:MM format to decimal hours.

    Args:
        hour: Hour in HH:MM format (e.g., "14:30")

    Returns:
        Decimal hours (e.g., 14.5 for 14:30)
    """
    hour_parts = hour.split(":")
    hour_int = int(hour_parts[0])
    minute_int = int(hour_parts[1])
    return hour_int + (minute_int / 60.0)


def get_normalized_time(hour_str: str) -> float:
    """Convert HH:MM to normalized value between 0.0 and 1.0."""
    if not hour_str:
        return 0.0

    parts = hour_str.split(":")
    h = int(parts[0])
    m = int(parts[1])
    total_seconds = (h * 3600) + (m * 60)
    return total_seconds / 86400.0


def create_time_based_plist(images_with_hours: List[Tuple[bytes, str]]) -> bytes:
    """Build plist for time-based HEIC metadata."""
    ti_entries = []
    temp_entries = []

    for idx, (_, hour) in enumerate(images_with_hours):
        if hour:
            norm_time = float(get_normalized_time(hour))
            temp_entries.append((int(idx), norm_time))

    temp_entries.sort(key=lambda x: x[1])

    for img_idx, norm_time in temp_entries:
        ti_entries.append({"i": img_idx, "t": norm_time})

    light_index = min(temp_entries, key=lambda x: abs(x[1] - 0.5))[0]

    def dist_to_midnight(t):
        return min(abs(t - 0.0), abs(t - 1.0))

    dark_index = min(temp_entries, key=lambda x: dist_to_midnight(x[1]))[0]

    plist_data = {"ti": ti_entries, "l": int(light_index), "d": int(dark_index)}
    logger.info(f"Plist Data: {plist_data}")
    return plistlib.dumps(plist_data, fmt=plistlib.FMT_BINARY)


def create_xmp_metadata(plist_bytes: bytes) -> bytes:
    """Build XMP XML expected by macOS for time-based dynamic wallpapers."""
    plist_b64 = base64.b64encode(plist_bytes).decode("ascii").replace("\n", "")

    xmp_content = f"""<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 5.4.0">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:apple_desktop="http://ns.apple.com/namespace/1.0/">
   <apple_desktop:h24>{plist_b64}</apple_desktop:h24>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""

    return xmp_content.encode("utf-8")
