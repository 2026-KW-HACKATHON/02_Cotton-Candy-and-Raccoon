-- Private subscriptions and durable delivery outbox. No provider call in collection transactions.
create table public.keyword_devices (
 owner_id uuid primary key,
 push_token text unique check (push_token ~ '^(ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_-]+\]$'),
 enabled boolean not null default false,
 activated_at timestamptz not null default clock_timestamp(),
 updated_at timestamptz not null default clock_timestamp()
);
create table public.keyword_subscriptions (
 owner_id uuid not null references public.keyword_devices on delete cascade,
 keyword text not null check (char_length(keyword) between 2 and 30),
 created_at timestamptz not null default clock_timestamp(),
 primary key(owner_id,keyword)
);
create table public.keyword_deliveries (
 id bigint generated always as identity primary key,
 owner_id uuid not null references public.keyword_devices on delete cascade,
 notice_id bigint not null references public.notices on delete cascade,
 matched_keywords text[] not null,
 title text not null,
 state text not null default 'queued' check(state in
  ('queued','sending','ticket','delivered','failed','unknown','cancelled')),
 attempts integer not null default 0,
 next_attempt_at timestamptz not null default now(),
 claim_token uuid,
 ticket_id text,
 sent_token text,
 error_code text,
 created_at timestamptz not null default now(),
 updated_at timestamptz not null default now(),
 unique(owner_id,notice_id)
);
create index keyword_deliveries_ready on public.keyword_deliveries(state,next_attempt_at);
alter table public.keyword_devices enable row level security;
alter table public.keyword_subscriptions enable row level security;
alter table public.keyword_deliveries enable row level security;
revoke all on public.keyword_devices,public.keyword_subscriptions,public.keyword_deliveries
 from public,anon,authenticated;
revoke all on sequence public.keyword_deliveries_id_seq from public,anon,authenticated;
grant all on public.keyword_devices,public.keyword_subscriptions,public.keyword_deliveries to service_role;
grant usage,select on sequence public.keyword_deliveries_id_seq to service_role;

-- PostgREST verifies the JWT before setting claims; client-supplied device IDs are never trusted.
create function public.keyword_owner() returns uuid language sql stable
set search_path = '' as $$
 select (nullif(current_setting('request.jwt.claims',true),'')::jsonb->>'sub')::uuid;
$$;
revoke all on function public.keyword_owner() from public,anon,authenticated;

create function public.get_keyword_preferences() returns jsonb language plpgsql security definer
set search_path = '' as $$
declare owner uuid := public.keyword_owner(); result jsonb;
begin
 if owner is null then raise exception 'authentication_required'; end if;
 select jsonb_build_object('enabled',d.enabled,'keywords',coalesce((
  select jsonb_agg(s.keyword order by s.created_at,s.keyword)
  from public.keyword_subscriptions s where s.owner_id=owner),'[]'::jsonb))
 into result from public.keyword_devices d where d.owner_id=owner;
 return coalesce(result,'{"enabled":false,"keywords":[]}'::jsonb);
end; $$;

create function public.save_keyword_preferences(p_keywords text[],p_enabled boolean,p_token text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare owner uuid := public.keyword_owner(); words text[]; old public.keyword_devices; t timestamptz;
begin
 if owner is null then raise exception 'authentication_required'; end if;
 if p_enabled is null or p_keywords is null or cardinality(p_keywords)>10
 or exists(select 1 from unnest(p_keywords) w where w is null) then
  raise exception 'invalid_keywords'; end if;
 select coalesce(array_agg(distinct lower(normalize(btrim(regexp_replace(w,'[[:space:]]+',' ','g'))))),array[]::text[])
 into words from unnest(p_keywords) w;
 if exists(select 1 from unnest(words) w where char_length(w) not between 2 and 30)
 then raise exception 'invalid_keywords'; end if;
 if p_enabled and (p_token is null or cardinality(words)=0) then
  raise exception 'push_token_and_keyword_required'; end if;
 if p_token is not null and (length(p_token)>250 or p_token !~ '^(ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_-]+\]$')
 then raise exception 'invalid_push_token'; end if;
 perform pg_advisory_xact_lock(hashtextextended(owner::text,87));
 t := clock_timestamp();
 select * into old from public.keyword_devices where owner_id=owner for update;
 if old.owner_id is not null and old.updated_at>t-interval '3 seconds' then
  raise exception 'update_rate_limited'; end if;
 insert into public.keyword_devices(owner_id,push_token,enabled,activated_at,updated_at)
 values(owner,p_token,p_enabled,t,t)
 on conflict(owner_id) do update set push_token=coalesce(excluded.push_token,keyword_devices.push_token),
 enabled=excluded.enabled, updated_at=t,
 activated_at=case when excluded.enabled and not keyword_devices.enabled then t
                  else keyword_devices.activated_at end;
 delete from public.keyword_subscriptions where owner_id=owner and not(keyword=any(words));
 insert into public.keyword_subscriptions(owner_id,keyword,created_at)
 select owner,w,t from unnest(words) w on conflict do nothing;
 update public.keyword_deliveries j set state='cancelled',updated_at=t
 where j.owner_id=owner and j.state='queued' and (not p_enabled or not exists(
  select 1 from public.keyword_subscriptions s where s.owner_id=owner
  and s.keyword=any(j.matched_keywords) and s.created_at<=j.created_at));
 return public.get_keyword_preferences();
end; $$;
revoke all on function public.get_keyword_preferences(),
 public.save_keyword_preferences(text[],boolean,text) from public,anon;
grant execute on function public.get_keyword_preferences(),
 public.save_keyword_preferences(text[],boolean,text) to authenticated;

create function public.enqueue_keyword_notice() returns trigger language plpgsql security definer
set search_path = '' as $$
begin
 if not new.is_visible or (new.category='dong' and new.dong_group is distinct from 'wolgye1')
 then return new; end if;
 insert into public.keyword_deliveries(owner_id,notice_id,matched_keywords,title)
 select d.owner_id,new.id,array_agg(s.keyword),new.title
 from public.keyword_devices d join public.keyword_subscriptions s on s.owner_id=d.owner_id
 where d.enabled and d.push_token is not null and s.created_at<=new.created_at
 and d.activated_at<=new.created_at
 -- Source dates have day precision: exclude historical backfill from earlier KST days.
 and new.registered_on >= (greatest(s.created_at,d.activated_at) at time zone 'Asia/Seoul')::date
 and strpos(lower(normalize(new.title || ' ' || coalesce(new.body_text,''))),s.keyword)>0
 group by d.owner_id on conflict(owner_id,notice_id) do nothing;
 return new;
end; $$;
revoke all on function public.enqueue_keyword_notice() from public,anon,authenticated;
-- Only INSERT: refreshing or editing an existing notice never creates new notifications.
create trigger enqueue_keyword_notice after insert on public.notices
for each row execute function public.enqueue_keyword_notice();

create function public.refresh_keyword_push_token(p_token text) returns void
language plpgsql security definer set search_path = '' as $$
declare owner uuid := public.keyword_owner();
begin
 if owner is null then raise exception 'authentication_required'; end if;
 if p_token is null or length(p_token)>250 or p_token !~ '^(ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_-]+\]$'
 then raise exception 'invalid_push_token'; end if;
 perform pg_advisory_xact_lock(hashtextextended(owner::text,87));
 update public.keyword_devices set push_token=p_token,updated_at=clock_timestamp()
 where owner_id=owner and enabled and push_token is distinct from p_token
 and updated_at<clock_timestamp()-interval '3 seconds';
end; $$;
revoke all on function public.refresh_keyword_push_token(text) from public,anon;
grant execute on function public.refresh_keyword_push_token(text) to authenticated;

create policy keyword_devices_service on public.keyword_devices for all to service_role
 using(true) with check(true);
create policy keyword_subscriptions_service on public.keyword_subscriptions for all to service_role
 using(true) with check(true);
create policy keyword_deliveries_service on public.keyword_deliveries for all to service_role
 using(true) with check(true);
