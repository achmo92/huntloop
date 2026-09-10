import sys
import yaml
import json
import httpx
from pathlib import Path

from huntloop.cli.main import EXIT_OK, EXIT_PARTIAL, EXIT_ABORTED
from huntloop.db.base import get_engine, make_session_factory
from huntloop.db.repository import CompanyRepository
from huntloop.db.models import Company
from huntloop.registry.resolve import resolve_employer, persist_resolution, ResolutionStatus
from huntloop.registry.staleness import staleness_message
from huntloop.discovery.fetch.page import StaticPageFetcher, RenderedPageFetcher

def _make_session():
    engine = get_engine()
    return make_session_factory(engine)()

def _make_client() -> httpx.Client:
    return httpx.Client(timeout=10.0, follow_redirects=True)

def _register_one(
    session,
    name: str,
    url: str | None,
    client: httpx.Client,
    static_fetcher: StaticPageFetcher | None,
    rendered_fetcher: RenderedPageFetcher | None,
    disabled: bool
) -> bool:
    """Returns True if successful, False if failed."""
    try:
        res = resolve_employer(
            name=name,
            careers_url=url,
            client=client,
            static_fetcher=static_fetcher,
            rendered_fetcher=rendered_fetcher,
        )
        persist_resolution(session, name, res, enabled=not disabled)
        session.commit()
        
        if res.status == ResolutionStatus.RESOLVED:
            print(f"[{name}] RESOLVED: {res.platform} ({res.slug})")
        elif res.status == ResolutionStatus.UNRESOLVED:
            print(f"[{name}] registered, not resolved")
        elif res.status == ResolutionStatus.AMBIGUOUS:
            print(f"[{name}] AMBIGUOUS. Candidates: " + ", ".join(f"{c.platform}({c.slug})" for c in res.candidates))
        return True
    except Exception as e:
        print(f"[{name}] error: {e}", file=sys.stderr)
        return False

def cmd_add(args) -> int:
    session = _make_session()
    client = _make_client()
    static_fetcher = StaticPageFetcher()
    rendered_fetcher = None if args.no_render else RenderedPageFetcher()
    try:
        ok = _register_one(
            session=session,
            name=args.name,
            url=args.url,
            client=client,
            static_fetcher=static_fetcher,
            rendered_fetcher=rendered_fetcher,
            disabled=args.disabled
        )
        return EXIT_OK if ok else EXIT_PARTIAL
    finally:
        session.close()

def cmd_import(args) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"error: {path} not found", file=sys.stderr)
        return EXIT_ABORTED
        
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"error parsing YAML: {e}", file=sys.stderr)
        return EXIT_ABORTED
        
    if not isinstance(data, list):
        print("error: expected a top-level list", file=sys.stderr)
        return EXIT_ABORTED
        
    session = _make_session()
    client = _make_client()
    static_fetcher = StaticPageFetcher()
    rendered_fetcher = None if args.no_render else RenderedPageFetcher()
    
    all_ok = True
    try:
        for item in data:
            if isinstance(item, str):
                name = item
                url = None
            elif isinstance(item, dict) and "name" in item:
                name = item["name"]
                url = item.get("url")
            else:
                print(f"error: malformed entry {item}, skipping", file=sys.stderr)
                all_ok = False
                continue
                
            ok = _register_one(
                session=session,
                name=name,
                url=url,
                client=client,
                static_fetcher=static_fetcher,
                rendered_fetcher=rendered_fetcher,
                disabled=False
            )
            if not ok:
                all_ok = False
                
        return EXIT_OK if all_ok else EXIT_PARTIAL
    finally:
        session.close()

def cmd_resolve(args) -> int:
    session = _make_session()
    client = _make_client()
    static_fetcher = StaticPageFetcher()
    rendered_fetcher = None if args.no_render else RenderedPageFetcher()
    try:
        repo = CompanyRepository(session)
        comp = repo.get_by_name(args.name)
        if not comp:
            print(f"error: unknown employer {args.name!r}", file=sys.stderr)
            return EXIT_ABORTED
            
        old_platform = comp.ats
        old_slug = comp.ats_identifier
        
        res = resolve_employer(
            name=comp.name,
            careers_url=comp.careers_url,
            client=client,
            static_fetcher=static_fetcher,
            rendered_fetcher=rendered_fetcher,
        )
        persist_resolution(session, comp.name, res)
        session.commit()
        
        if res.status == ResolutionStatus.RESOLVED:
            old_plat_str = old_platform.value if old_platform else "None"
            print(f"[{comp.name}] RESOLVED: {old_plat_str}({old_slug}) -> {res.platform}({res.slug})")
        else:
            print(f"[{comp.name}] {res.status.name}")
            
        return EXIT_OK
    finally:
        session.close()

def cmd_list(args) -> int:
    session = _make_session()
    try:
        query = session.query(Company).order_by(Company.name)
        companies = query.all()
        
        if args.json:
            out = []
            for c in companies:
                stale_msg = staleness_message(c)
                out.append({
                    "name": c.name,
                    "platform": c.ats.value if c.ats else None,
                    "slug": c.ats_identifier,
                    "enabled": c.enabled,
                    "last_job_count": c.last_job_count,
                    "stale": stale_msg
                })
            print(json.dumps(out, indent=2))
            return EXIT_OK
            
        # Human output
        print(f"{'NAME':<20} {'PLATFORM':<15} {'SLUG':<20} {'ENABLED':<8} {'JOBS':<5} {'STALE'}")
        for c in companies:
            stale = staleness_message(c) or ""
            platform = c.ats.value if c.ats else "-"
            slug = c.ats_identifier or "-"
            enabled = "Yes" if c.enabled else "No"
            jobs = str(c.last_job_count) if c.last_job_count is not None else "-"
            print(f"{c.name:<20} {platform:<15} {slug:<20} {enabled:<8} {jobs:<5} {stale}")
        return EXIT_OK
    finally:
        session.close()
