"""Small pagination helpers shared by internal list pilots."""

ALLOWED_PAGE_SIZES = (20, 50, 100)
DEFAULT_PAGE_SIZE = 20
PAGINATION_WINDOW = 2


def parse_page_size(params, *, allowed=ALLOWED_PAGE_SIZES, default=DEFAULT_PAGE_SIZE):
    """
    PRE: params is a QueryDict-like mapping from an untrusted GET request.
    POST: returns an allowed page size; invalid or missing values fall back to default.
    """
    raw_value = params.get('page_size')
    try:
        page_size = int(raw_value)
    except (TypeError, ValueError):
        return default
    return page_size if page_size in allowed else default


def build_pagination_page_numbers(page_obj, *, window=PAGINATION_WINDOW):
    """
    PRE: page_obj is a Django Page with at least one page.
    POST: returns page numbers with None markers for ellipsis gaps.
    """
    total_pages = page_obj.paginator.num_pages
    current = page_obj.number
    selected = {1, total_pages}
    selected.update(
        page
        for page in range(current - window, current + window + 1)
        if 1 <= page <= total_pages
    )
    page_numbers = []
    previous = None
    for page in sorted(selected):
        if previous is not None and page > previous + 1:
            page_numbers.append(None)
        page_numbers.append(page)
        previous = page
    return page_numbers
