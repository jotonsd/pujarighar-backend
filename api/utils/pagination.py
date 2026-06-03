from django.core.paginator import Paginator
from rest_framework.request import Request


def paginate_queryset(queryset, request: Request, default_page_size: int = 20):
    """
    Paginate any queryset or list using ?page= and ?page_size= query params.

    Returns:
        page_items  — list of objects for the current page
        pagination  — dict with page, page_size, total, total_pages
    """
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    try:
        page_size = max(1, min(100, int(request.query_params.get("page_size", default_page_size))))
    except (TypeError, ValueError):
        page_size = default_page_size

    paginator = Paginator(queryset, page_size)
    page_obj  = paginator.get_page(page)

    return list(page_obj.object_list), {
        "page":        page_obj.number,
        "page_size":   page_size,
        "total":       paginator.count,
        "total_pages": paginator.num_pages,
    }
