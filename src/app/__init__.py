"""The tagger review app: a local web app where a person approves or corrects the tagger's output.

Plan and progress: docs/app/PLAN.md. The server calls the scraper, pipeline and evaluation code as
functions; it never shells out to the `tagger` command.
"""
