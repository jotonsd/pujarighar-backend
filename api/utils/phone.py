_BENGALI_DIGITS = str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789')


def to_english_digits(text: str) -> str:
    """Converts Bangla numerals (০-৯) to plain ASCII digits — shipping/
    contact phone numbers are free-typed and sometimes entered via a
    Bangla keyboard rather than Latin digits. External APIs (the SMS
    gateway, courier providers like Pathao/Steadfast) only accept ASCII
    digits, so every outbound phone number should be passed through this
    first."""
    return (text or '').translate(_BENGALI_DIGITS)
