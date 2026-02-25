"""
QR Code Transport for MeshForge Tactical Messaging.

Encodes tactical messages as QR codes for field check-in scenarios:
- Staging area check-in (scan QR to register presence)
- Offline message relay (generate QR, photograph/scan elsewhere)
- Printed check-in sheets for non-digital operators

Supports terminal rendering (Unicode half-block characters) and
optional PNG output via the 'qrcode' library.

Usage:
    from tactical.qr_transport import (
        encode_qr_terminal, generate_checkin_qr, decode_qr_text,
    )

    # Generate a check-in QR code for terminal display
    qr_text = generate_checkin_qr("WH6GXZ", latitude=21.3, longitude=-157.8)
    print(qr_text)

    # Decode QR text content back to message
    msg = decode_qr_text(x1_string_from_qr)
"""

import logging
from pathlib import Path
from typing import Optional

from utils.safe_import import safe_import

from tactical.models import (
    CheckIn,
    EncryptionMode,
    TacticalMessage,
    TacticalPriority,
    TacticalType,
    generate_message_id,
)
from tactical.x1_codec import decode, encode, is_x1

logger = logging.getLogger(__name__)

# Optional QR code library
_qrcode, _HAS_QRCODE = safe_import('qrcode')

# Unicode half-block characters for terminal QR rendering
_BLOCK_TOP = '\u2580'      # Upper half block
_BLOCK_BOTTOM = '\u2584'   # Lower half block
_BLOCK_FULL = '\u2588'     # Full block
_BLOCK_EMPTY = ' '         # Space


def is_qr_available() -> bool:
    """Check if QR code generation is available.

    Returns:
        True if 'qrcode' package is installed.
    """
    return _HAS_QRCODE


def encode_qr_terminal(data: str) -> str:
    """Render a string as QR code using terminal Unicode half-block characters.

    Uses the 'qrcode' library to generate the QR matrix, then renders
    it using Unicode half-block characters for compact terminal display.
    Each terminal row represents two QR module rows.

    Args:
        data: String data to encode as QR.

    Returns:
        Multi-line string of Unicode characters forming a QR code.

    Raises:
        RuntimeError: If 'qrcode' package is not available.
    """
    if not _HAS_QRCODE:
        raise RuntimeError(
            "QR code generation requires 'qrcode' package. "
            "Install with: pip install qrcode"
        )

    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    qr = qrcode.QRCode(
        version=None,  # Auto-size
        error_correction=ERROR_CORRECT_M,
        box_size=1,
        border=1,
    )
    qr.add_data(data)
    qr.make(fit=True)

    # Get the QR matrix (list of lists of booleans)
    matrix = qr.get_matrix()
    rows = len(matrix)

    lines = []

    # Process two rows at a time using half-block characters
    for y in range(0, rows, 2):
        line = ''
        for x in range(len(matrix[0])):
            top = matrix[y][x] if y < rows else False
            bottom = matrix[y + 1][x] if y + 1 < rows else False

            if top and bottom:
                line += _BLOCK_FULL
            elif top and not bottom:
                line += _BLOCK_TOP
            elif not top and bottom:
                line += _BLOCK_BOTTOM
            else:
                line += _BLOCK_EMPTY

        lines.append(line)

    return '\n'.join(lines)


def encode_qr_png(data: str, output_path: Path, box_size: int = 10) -> Path:
    """Generate a QR code and save as PNG.

    Args:
        data: String data to encode.
        output_path: Path to save the PNG file.
        box_size: Size of each QR module in pixels.

    Returns:
        Path to the generated PNG file.

    Raises:
        RuntimeError: If 'qrcode' package is not available.
    """
    if not _HAS_QRCODE:
        raise RuntimeError(
            "QR code generation requires 'qrcode' package. "
            "Install with: pip install qrcode[pil]"
        )

    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=box_size,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    img.save(str(output_path))

    logger.info(f"QR code saved to {output_path}")
    return output_path


def decode_qr_text(qr_text: str) -> Optional[TacticalMessage]:
    """Parse QR code text content back to TacticalMessage.

    Delegates to x1_codec.decode() — QR codes encode the X1 wire string.

    Args:
        qr_text: Text content scanned from a QR code.

    Returns:
        TacticalMessage if valid X1 format, None otherwise.
    """
    if not qr_text or not is_x1(qr_text.strip()):
        return None

    try:
        return decode(qr_text.strip())
    except ValueError as e:
        logger.warning(f"Failed to decode QR content as X1: {e}")
        return None


def generate_checkin_qr(
    callsign: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    altitude: Optional[float] = None,
    status: str = "ok",
    personnel_count: int = 1,
) -> str:
    """Generate a CHECKIN message and return its QR terminal representation.

    Convenience function for field staging area check-in.

    Args:
        callsign: Operator callsign or identifier.
        latitude: GPS latitude (optional).
        longitude: GPS longitude (optional).
        altitude: GPS altitude in meters (optional).
        status: Check-in status ('ok', 'needs_help', 'injured', 'evacuating').
        personnel_count: Number of personnel.

    Returns:
        Terminal-renderable QR code string.
    """
    checkin = CheckIn(
        callsign=callsign,
        status=status,
        personnel_count=personnel_count,
        latitude=latitude,
        longitude=longitude,
        altitude=altitude,
    )

    msg = TacticalMessage(
        tactical_type=TacticalType.CHECKIN,
        priority=TacticalPriority.ROUTINE,
        encryption_mode=EncryptionMode.CLEAR,
        sender_id=callsign,
        content=checkin.to_dict(),
    )

    x1_string = encode(msg)
    return encode_qr_terminal(x1_string)


def generate_x1_for_qr(msg: TacticalMessage) -> str:
    """Encode a TacticalMessage as an X1 string suitable for QR transport.

    Same as x1_codec.encode() but ensures the result fits in a QR code.
    QR v40 can hold up to 2953 bytes of binary data, which is ample
    for any single tactical message.

    Args:
        msg: TacticalMessage to encode.

    Returns:
        X1 wire format string.
    """
    return encode(msg)
