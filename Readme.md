At the moment, this library is a work in progress. It currently reads the
IL State Board of Elections Reports Filed feed, and is able to parse A1 and D2
reports and return python objects containing data about the contributions
therein.

The library exposes three public methods:

*   scrape_reports_filed(): Returns list of all recent reports, in
    reverse-chronological order. You'll probably want to pay attention to the  
    report ID in order to avoid duplication - the feed, for some reason, seems
    to contain multiple entries for some but not all reports, assigning them
    to multiple committees. The library is capable of determining the correct
    committee when given the report ID, however.

*   scrape_a1(report_id, report_url, report_date): Downloads and processes an
    A1 report, returning a dict with keys 'contribs' (containing a list of
    individual contributions, also as dicts); 'committee_id' (the ID of the
    filing committee); and 'committee_name' (the name of the filing committee).

*   scrape_d2(report_id): Given a report ID, returns a 3-tuple of individual
    contributions (as a list); transfers-in (as a list); and report metadata
    (as a dict). The report metadata contains the summary lines for each
    separate category in the D2 report.
