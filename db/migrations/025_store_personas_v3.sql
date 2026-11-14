-- Richie's, engineering PRD V5 (14 Nov): persona signals and Looks for you eligibility.
--  * personas: no age anywhere (the description now carries none and is optional); archetype, how the shopper
--    arrived, the occasion they arrived with, and the starting-signal lines shown in the switcher
--  * events: the arrival itself (entry), look card views and taps by placement, and the moment a session became
--    eligible for Looks for you

alter table store_personas alter column description drop not null;
alter table store_personas add column if not exists archetype text;
alter table store_personas add column if not exists entry_source text
    check (entry_source in ('direct', 'notification', 'web_search', 'product_search'));
alter table store_personas add column if not exists entry_occasion text;
alter table store_personas add column if not exists starting_signals jsonb not null default '[]';

alter table store_events drop constraint if exists store_events_type_check;
alter table store_events add constraint store_events_type_check
    check (type in ('view', 'save', 'purchase', 'add_to_bag', 'add_look_to_bag', 'size_select', 'filter_apply',
                    'occasion_select', 'refine_apply', 'shortlist_view', 'look_view', 'swap_out', 'swap_in',
                    'search_referral', 'entry', 'look_card_view', 'look_card_tap', 'looks_eligible'));
