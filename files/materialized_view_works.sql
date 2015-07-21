create materialized view mv_works_editions_datasources_identifiers
as
 SELECT editions.id AS editions_id,
    editions.data_source_id,
    editions.primary_identifier_id,
    editions.sort_title,
    editions.permanent_work_id,
    editions.sort_author,
    editions.language,
    editions.cover_full_url,
    editions.cover_thumbnail_url,
    editions.open_access_download_url,
    datasources.name,
    identifiers.type,
    identifiers.identifier,
    works.id AS works_id,
    works.audience,
    works.target_age,
    works.fiction,
    works.quality,
    works.rating,
    works.popularity,
    works.random,
    works.last_update_time,
    works.simple_opds_entry,
    licensepools.id AS license_pool_id
   FROM works
     JOIN editions ON editions.work_id = works.id AND editions.is_primary_for_work = true
     JOIN licensepools ON editions.data_source_id = licensepools.data_source_id AND editions.primary_identifier_id = licensepools.identifier_id
     JOIN datasources ON editions.data_source_id = datasources.id
     JOIN identifiers on editions.primary_identifier_id = identifiers.id
  WHERE works.was_merged_into_id IS NULL AND works.presentation_ready = true AND editions.medium = 'Book'::medium AND works.simple_opds_entry IS NOT NULL
  ORDER BY editions.sort_title, editions.sort_author;

create index mv_works_editions_adult_fiction_author_ds_id on mv_works_editions_datasources_identifiers (sort_author, sort_title, license_pool_id) WHERE audience in ('Adult', 'Adults Only') AND fiction = true AND language = 'eng';
create index mv_works_editions_adult_fiction_author_iden on mv_works_editions_datasources_identifiers (sort_author, sort_title, works_id, license_pool_id) WHERE audience in ('Adult', 'Adults Only') AND fiction = true AND language = 'eng';
create index mv_works_editions_adult_fiction_author_other_iden on mv_works_editions_datasources_identifiers (sort_author, sort_title, works_id, language) WHERE audience in ('Adult', 'Adults Only') AND fiction = true AND language <> 'eng';
create index mv_works_editions_adult_fiction_author_w_ds_id on mv_works_editions_datasources_identifiers (sort_author, sort_title, works_id, license_pool_id) WHERE audience in ('Adult', 'Adults Only') AND fiction = true AND language = 'eng';
create index mv_works_editions_adult_fiction_title_ds_id on mv_works_editions_datasources_identifiers (sort_title, sort_author, license_pool_id) WHERE audience in ('Adult', 'Adults Only') AND fiction = true AND language = 'eng';
create index mv_works_editions_adult_fiction_title_iden on mv_works_editions_datasources_identifiers (sort_title, sort_author, works_id, license_pool_id) WHERE audience in ('Adult', 'Adults Only') AND fiction = true AND language = 'eng';
create index mv_works_editions_adult_fiction_title_other_iden on mv_works_editions_datasources_identifiers (sort_title, sort_author, works_id, language) WHERE audience in ('Adult', 'Adults Only') AND fiction = true AND language <> 'eng';
create index mv_works_editions_adult_fiction_title_w_ds_id on mv_works_editions_datasources_identifiers (sort_title, sort_author, works_id, license_pool_id) WHERE audience in ('Adult', 'Adults Only') AND fiction = true AND language = 'eng';
create index mv_works_editions_adult_nfiction_author_iden on mv_works_editions_datasources_identifiers (sort_author, sort_title, works_id, license_pool_id) WHERE audience in ('Adult', 'Adults Only') AND fiction = false AND language = 'eng';
create index mv_works_editions_adult_nfiction_author_other_iden on mv_works_editions_datasources_identifiers (sort_author, sort_title, works_id, language) WHERE audience in ('Adult', 'Adults Only') AND fiction = false AND language <> 'eng';
create index mv_works_editions_adult_nfiction_title_iden on mv_works_editions_datasources_identifiers (sort_title, sort_author, works_id, license_pool_id) WHERE audience in ('Adult', 'Adults Only') AND fiction = false AND language = 'eng';
create index mv_works_editions_adult_nfiction_title_other_iden on mv_works_editions_datasources_identifiers (sort_title, sort_author, works_id, language) WHERE audience in ('Adult', 'Adults Only') AND fiction = false AND language <> 'eng';
create index mv_works_editions_yachild_fiction_author_iden on mv_works_editions_datasources_identifiers (sort_author, sort_title, language, works_id) WHERE audience in ('Children', 'Young Adult') AND fiction = true;
create index mv_works_editions_yachild_nonfiction_author_iden on mv_works_editions_datasources_identifiers (sort_author, sort_title, language, works_id) WHERE audience in ('Children', 'Young Adult') AND fiction = false;
create index mv_works_editions_yachild_nonfiction_title_iden on mv_works_editions_datasources_identifiers (sort_title, sort_author, language, works_id) WHERE audience in ('Children', 'Young Adult') AND fiction = false;
;
create unique index mv_works_editions_work_id on mv_works_editions_datasources_identifiers (works_id);