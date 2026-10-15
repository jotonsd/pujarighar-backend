_BENGALI_DIGITS = str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789')


def to_english_digits(text: str) -> str:
    """Converts Bangla numerals (০-৯) to plain ASCII digits — shipping/
    contact phone numbers are free-typed and sometimes entered via a
    Bangla keyboard rather than Latin digits. External APIs (the SMS
    gateway, courier providers like Pathao/Steadfast) only accept ASCII
    digits, so every outbound phone number should be passed through this
    first. Also strips hyphens and spaces (e.g. '01824-527774' typed with
    a visual grouping dash) — courier APIs reject anything but a bare
    digit string and error out instead of just ignoring the extra chars."""
    return (text or '').translate(_BENGALI_DIGITS).replace('-', '').replace(' ', '')
