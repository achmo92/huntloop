import sys
import json
from huntloop.cli.main import EXIT_OK
from huntloop.cli.render import render_jobs_table
from huntloop.db.base import get_engine, make_session_factory
from huntloop.db.models import Job, Company
from sqlalchemy.orm import joinedload
from sqlalchemy import desc

def _make_session():
    engine = get_engine()
    return make_session_factory(engine)()

def cmd_jobs_list(args) -> int:
    session = _make_session()
    try:
        query = session.query(Job, Company).join(Company, Job.company_id == Company.id)
        
        if args.min_score is not None:
            query = query.filter(Job.score_overall >= args.min_score)
            
        if args.company:
            query = query.filter(Company.name == args.company)
            
        # No default score floor. TRAK-06's principle is that nothing is suppressed; a CLI that hid low scorers by default would reintroduce exactly the suppression the requirement forbids, one layer up. --min-score exists so filtering is a choice the user makes visibly.
        
        query = query.order_by(Job.score_overall.desc().nulls_last(), Job.first_seen_at.desc())
        
        # apply limit before in-memory filtering for flags?
        if not args.flagged and args.limit:
            query = query.limit(args.limit)
            
        rows = query.all()
        
        # Unpack rows and bind company to job manually
        jobs = []
        for j, c in rows:
            j.company = c
            jobs.append(j)
            
        if args.flagged:
            filtered_jobs = []
            for j in jobs:
                if j.score_flags:
                    for v in j.score_flags.values():
                        if isinstance(v, dict) and v.get("raised"):
                            filtered_jobs.append(j)
                            break
            if args.limit:
                filtered_jobs = filtered_jobs[:args.limit]
            jobs = filtered_jobs
            
        if args.json:
            out = []
            for j in jobs:
                out.append({
                    "id": str(j.id),
                    "title": j.title,
                    "company": j.company.name,
                    "location": j.location_normalized or j.location_raw,
                    "score_overall": float(j.score_overall) if j.score_overall is not None else None,
                    "flags": j.score_flags,
                    "url": j.url
                })
            print(json.dumps(out, indent=2))
        else:
            print(render_jobs_table(jobs))
            
        return EXIT_OK
    finally:
        session.close()
