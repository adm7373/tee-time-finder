from datetime import time

from .models import SearchRequest, TeeTimeOption


class SearchProvider:
    """Provider boundary used by real course integrations later."""

    name = "mock"

    def search(self, request: SearchRequest) -> list[TeeTimeOption]:
        candidates = [
            ("Quincy Municipal Golf Course", time(7, 30)),
            ("Granite Links Golf Club", time(9, 10)),
            ("George Wright Golf Course", time(11, 45)),
        ]
        earliest = request.earliest_time or time.min
        latest = request.latest_time or time.max
        return [
            TeeTimeOption(
                provider=self.name,
                course_name=course,
                start_time=start,
                players=request.players,
                booking_enabled=False,
            )
            for course, start in candidates
            if earliest <= start <= latest
        ]


def get_search_providers() -> list[SearchProvider]:
    return [SearchProvider()]


def search_all(request: SearchRequest) -> list[TeeTimeOption]:
    results = [result for provider in get_search_providers() for result in provider.search(request)]
    return sorted(results, key=lambda result: result.start_time)

