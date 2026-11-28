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


def to_local_bd_phone(text: str) -> str:
    """Normalizes to the local 01XXXXXXXXX form couriers (Pathao/Steadfast)
    expect when assigning an order to them — numbers are normally stored
    this way already, but a '+880'/'880' country code sometimes slips in
    (free-typed, pasted from a contact card, etc.) and gets rejected
    outright by the courier API instead of being normalized server-side."""
    digits = to_english_digits(text).lstrip('+')
    if digits.startswith('880') and len(digits) == 13:
        return '0' + digits[3:]
    return digits
