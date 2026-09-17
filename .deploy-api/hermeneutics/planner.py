"""Deterministic bounded retrieval; never accepts model-generated SQL."""

from datetime import timedelta

from .models import Period, RetrievalOperation


def plan(request):
    frame = request.frame
    comparison = frame.comparison
    if comparison is None and frame.datasets:
        length = (frame.period.end - frame.period.start).days + 1
        comparison = Period(start=frame.period.start - timedelta(days=length),
                            end=frame.period.start - timedelta(days=1))
    operations = []
    for dataset in frame.datasets:
        operations.append(RetrievalOperation(kind="structured", dataset=dataset, period=frame.period,
                                              purpose="Measure the analysis period"))
        operations.append(RetrievalOperation(kind="structured", dataset=dataset, period=comparison,
                                              purpose="Measure the comparison period"))
    if frame.include_documents:
        operations.append(RetrievalOperation(kind="semantic", query=request.question,
                                              purpose="Find relevant source documents, including contrary context"))
    if frame.include_previous_interpretations:
        operations.append(RetrievalOperation(kind="previous", query=request.question,
                                              purpose="Inspect prior hypotheses, not independent corroboration"))
    return operations
