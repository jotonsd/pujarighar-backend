import json
import logging

from google import genai
from google.genai import types

from api.models import SiteSetting

logger = logging.getLogger(__name__)


def detect_district_thana(address: str) -> dict:
    """Asks Gemini to pull the Bangladesh district (zilla) and thana/upazila
    out of a free-typed shipping address — backs the admin shipping form's
    refresh button next to District/Thana, since a customer's written
    address (especially from the app/website, never structured) often
    buries these in the middle of a sentence rather than as clean fields.
    Reuses the same genai.Client(api_key=...) call shape as
    support_chat_service.describe_image_for_search."""
    s = SiteSetting.get()
    if not s.gemini_api_key:
        raise RuntimeError('Gemini API key is not configured')

    client = genai.Client(api_key=s.gemini_api_key)
    model = s.gemini_model or 'gemini-3.6-flash'
    response = client.models.generate_content(
        model=model,
        contents=[types.Content(role='user', parts=[types.Part(text=(
            'Extract the Bangladesh district (zilla) and thana/upazila from this '
            'shipping address, written in Bengali or English. Reply with ONLY '
            'compact JSON: {"district": "...", "thana": "..."} — use the standard '
            'English spelling for each (e.g. "Dhaka", "Mirpur"). If either cannot '
            'be determined from the address, use an empty string for it.\n\n'
            f'Address: {address[:500]}'
        ))])],
        config=types.GenerateContentConfig(response_mime_type='application/json'),
    )
    try:
        data = json.loads(response.text or '{}')
    except ValueError:
        logger.error(f'detect_district_thana: non-JSON Gemini response: {response.text!r}')
        return {'district': '', 'thana': ''}
    return {
        'district': (data.get('district') or '').strip(),
        'thana': (data.get('thana') or '').strip(),
    }
