{#
  Functions that DuckDB and Databricks (Spark SQL) spell differently.
  Models call these macros; dbt picks the version for the active adapter
  (duckdb__... or databricks__...), so the same models run on both engines.
#}

{# text -> timestamp with a strftime-style format; NULL when it does not parse #}
{% macro try_parse_ts(col, fmt) -%}
    {{ return(adapter.dispatch('try_parse_ts', 'retail_intelligence')(col, fmt)) }}
{%- endmacro %}

{% macro default__try_parse_ts(col, fmt) -%}
    try_strptime({{ col }}, '{{ fmt }}')
{%- endmacro %}

{% macro databricks__try_parse_ts(col, fmt) -%}
    {%- set java = fmt
        | replace('%Y', 'yyyy') | replace('%m', 'MM') | replace('%d', 'dd')
        | replace('%H', 'HH') | replace('%M', 'mm') | replace('%S', 'ss')
        | replace('%b', 'MMM') -%}
    {#- an ISO 'T' between date and time: swap it for a space on both sides (no quoting needed) -#}
    {%- if 'T' in java -%}
    try_to_timestamp(replace({{ col }}, 'T', ' '), '{{ java | replace('T', ' ') }}')
    {%- else -%}
    try_to_timestamp({{ col }}, '{{ java }}')
    {%- endif -%}
{%- endmacro %}


{# whole days from start_date to end_date (end - start) #}
{% macro days_between(start_date, end_date) -%}
    {{ return(adapter.dispatch('days_between', 'retail_intelligence')(start_date, end_date)) }}
{%- endmacro %}

{% macro default__days_between(start_date, end_date) -%}
    date_diff('day', {{ start_date }}, {{ end_date }})
{%- endmacro %}

{% macro databricks__days_between(start_date, end_date) -%}
    datediff({{ end_date }}, {{ start_date }})
{%- endmacro %}


{# FROM-clause fragment: one row per day from first_day to last_day (inclusive), column `alias_col` #}
{# correlated=true when the bounds come from a table earlier in the same FROM clause #}
{% macro day_series(first_day, last_day, alias='t', alias_col='d', correlated=true) -%}
    {{ return(adapter.dispatch('day_series', 'retail_intelligence')(first_day, last_day, alias, alias_col, correlated)) }}
{%- endmacro %}

{% macro default__day_series(first_day, last_day, alias, alias_col, correlated) -%}
    range({{ first_day }}, {{ last_day }} + interval 1 day, interval 1 day) {{ alias }}({{ alias_col }})
{%- endmacro %}

{% macro databricks__day_series(first_day, last_day, alias, alias_col, correlated) -%}
    {% if correlated %}lateral {% endif %}(select explode(sequence(cast({{ first_day }} as date), cast({{ last_day }} as date), interval 1 day)) as {{ alias_col }}) {{ alias }}
{%- endmacro %}


{# lower case, accents removed #}
{% macro strip_accents_lower(col) -%}
    {{ return(adapter.dispatch('strip_accents_lower', 'retail_intelligence')(col)) }}
{%- endmacro %}

{% macro default__strip_accents_lower(col) -%}
    lower(strip_accents({{ col }}))
{%- endmacro %}

{% macro databricks__strip_accents_lower(col) -%}
    translate(lower({{ col }}), 'áàâäãåéèêëíìîïóòôöõúùûüñçýÿ', 'aaaaaaeeeeiiiiooooouuuuncyy')
{%- endmacro %}


{# regexp_replace of every match (DuckDB needs the 'g' flag, Spark replaces all by default) #}
{% macro regexp_replace_all(col, pattern, replacement) -%}
    {{ return(adapter.dispatch('regexp_replace_all', 'retail_intelligence')(col, pattern, replacement)) }}
{%- endmacro %}

{% macro default__regexp_replace_all(col, pattern, replacement) -%}
    regexp_replace({{ col }}, '{{ pattern }}', '{{ replacement }}', 'g')
{%- endmacro %}

{% macro databricks__regexp_replace_all(col, pattern, replacement) -%}
    regexp_replace({{ col }}, '{{ pattern }}', '{{ replacement }}')
{%- endmacro %}


{# ISO day of week: 1 = Monday ... 7 = Sunday #}
{% macro iso_dow(col) -%}
    {{ return(adapter.dispatch('iso_dow', 'retail_intelligence')(col)) }}
{%- endmacro %}

{% macro default__iso_dow(col) -%}
    isodow({{ col }})
{%- endmacro %}

{% macro databricks__iso_dow(col) -%}
    extract(dayofweek_iso from {{ col }})
{%- endmacro %}


{# ---------------------------------------------------------------- fuzzy title matching
   DuckDB has jaro_winkler_similarity and damerau_levenshtein built in. On Databricks they
   are Unity Catalog Python functions, created by create_cross_engine_udfs() at run start. #}
{% macro title_similarity(a, b) -%}
    {{ return(adapter.dispatch('title_similarity', 'retail_intelligence')(a, b)) }}
{%- endmacro %}
{% macro default__title_similarity(a, b) -%} jaro_winkler_similarity({{ a }}, {{ b }}) {%- endmacro %}
{% macro databricks__title_similarity(a, b) -%} {{ target.catalog }}.silver.ri_jaro_winkler({{ a }}, {{ b }}) {%- endmacro %}

{% macro title_edits(a, b) -%}
    {{ return(adapter.dispatch('title_edits', 'retail_intelligence')(a, b)) }}
{%- endmacro %}
{% macro default__title_edits(a, b) -%} damerau_levenshtein({{ a }}, {{ b }}) {%- endmacro %}
{% macro databricks__title_edits(a, b) -%} {{ target.catalog }}.silver.ri_damerau_levenshtein({{ a }}, {{ b }}) {%- endmacro %}

{% macro create_cross_engine_udfs() %}
  {% if target.type == 'databricks' %}
    {% set stmt %}
create schema if not exists {{ target.catalog }}.silver
    {% endset %}
    {% do run_query(stmt) %}
    {% set stmt %}
create or replace function {{ target.catalog }}.silver.ri_jaro_winkler(a string, b string)
    returns double
    language python
    comment 'Jaro-Winkler similarity (prefix scale 0.1, up to 4 characters), as DuckDB jaro_winkler_similarity'
    as $$
if a is None or b is None:
    return None
if a == b:
    return 1.0
la, lb = len(a), len(b)
if la == 0 or lb == 0:
    return 0.0
window = max(max(la, lb) // 2 - 1, 0)
ma, mb = [False] * la, [False] * lb
matches = 0
for i in range(la):
    for j in range(max(0, i - window), min(lb, i + window + 1)):
        if not mb[j] and a[i] == b[j]:
            ma[i] = mb[j] = True
            matches += 1
            break
if matches == 0:
    return 0.0
k = transpositions = 0
for i in range(la):
    if ma[i]:
        while not mb[k]:
            k += 1
        if a[i] != b[k]:
            transpositions += 1
        k += 1
jaro = (matches / la + matches / lb + (matches - transpositions / 2) / matches) / 3
prefix = 0
for x, y in zip(a[:4], b[:4]):
    if x != y:
        break
    prefix += 1
return jaro + prefix * 0.1 * (1 - jaro)
$$
    {% endset %}
    {% do run_query(stmt) %}
    {% set stmt %}
create or replace function {{ target.catalog }}.silver.ri_damerau_levenshtein(a string, b string)
    returns int
    language python
    comment 'Edit distance counting insertions, deletions, substitutions and adjacent transpositions'
    as $$
if a is None or b is None:
    return None
la, lb = len(a), len(b)
d = [[0] * (lb + 1) for _ in range(la + 1)]
for i in range(la + 1):
    d[i][0] = i
for j in range(lb + 1):
    d[0][j] = j
for i in range(1, la + 1):
    for j in range(1, lb + 1):
        cost = 0 if a[i - 1] == b[j - 1] else 1
        d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
        if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
            d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
return d[la][lb]
$$
    {% endset %}
    {% do run_query(stmt) %}
  {% endif %}
  select 1
{% endmacro %}


{# ---------------------------------------------------------------- files read by the tests #}
{% macro read_json_file(path) -%}
    {{ return(adapter.dispatch('read_json_file', 'retail_intelligence')(path)) }}
{%- endmacro %}
{% macro default__read_json_file(path) -%} read_json_auto('{{ path }}') {%- endmacro %}
{% macro databricks__read_json_file(path) -%} read_files('{{ path }}', format => 'json', multiLine => true) {%- endmacro %}

{% macro array_length(col) -%}
    {{ return(adapter.dispatch('array_length', 'retail_intelligence')(col)) }}
{%- endmacro %}
{% macro default__array_length(col) -%} len({{ col }}) {%- endmacro %}
{% macro databricks__array_length(col) -%} size({{ col }}) {%- endmacro %}


{# ---------------------------------------------------------------- small syntax differences #}
{# "all columns but these": DuckDB * EXCLUDE (...), Databricks * EXCEPT (...) #}
{% macro star_except(cols, prefix='') -%}
    {{ return(adapter.dispatch('star_except', 'retail_intelligence')(cols, prefix)) }}
{%- endmacro %}
{% macro default__star_except(cols, prefix) -%} {{ prefix }}* exclude ({{ cols | join(', ') }}) {%- endmacro %}
{% macro databricks__star_except(cols, prefix) -%} {{ prefix }}* except ({{ cols | join(', ') }}) {%- endmacro %}

{# last non-null value of a window frame #}
{% macro last_value_ignore_nulls(expr) -%}
    {{ return(adapter.dispatch('last_value_ignore_nulls', 'retail_intelligence')(expr)) }}
{%- endmacro %}
{% macro default__last_value_ignore_nulls(expr) -%} last_value({{ expr }} ignore nulls) {%- endmacro %}
{% macro databricks__last_value_ignore_nulls(expr) -%} last_value({{ expr }}, true) {%- endmacro %}


{# UNION ALL matching columns by name (a column missing in one input becomes NULL there).
   DuckDB has UNION ALL BY NAME; for Spark the column list is built from the relations. #}
{% macro union_all_by_name(relations) -%}
    {{ return(adapter.dispatch('union_all_by_name', 'retail_intelligence')(relations)) }}
{%- endmacro %}

{% macro default__union_all_by_name(relations) -%}
    {%- for r in relations %}
    select * from {{ r }}{% if not loop.last %}
    union all by name{% endif %}
    {%- endfor %}
{%- endmacro %}

{% macro databricks__union_all_by_name(relations) -%}
    {%- if not execute -%}
    select 1 as placeholder
    {%- else -%}
    {%- set names, types, per_rel = [], {}, [] -%}
    {%- for r in relations -%}
        {%- set cols = adapter.get_columns_in_relation(r) -%}
        {%- do per_rel.append(cols | map(attribute='name') | list) -%}
        {%- for c in cols if c.name not in names -%}
            {%- do names.append(c.name) -%}{%- do types.update({c.name: c.data_type}) -%}
        {%- endfor -%}
    {%- endfor -%}
    {%- for r in relations %}
    {%- set have = per_rel[loop.index0] %}
    select {% for n in names %}{{ ('`' ~ n ~ '`') if n in have else ('cast(null as ' ~ types[n] ~ ') as `' ~ n ~ '`') }}{% if not loop.last %}, {% endif %}{% endfor %}
    from {{ r }}{% if not loop.last %}
    union all{% endif %}
    {%- endfor %}
    {%- endif -%}
{%- endmacro %}


{# date -> text with a strftime-style format (%Y %m %d %a supported) #}
{% macro format_date(col, fmt) -%}
    {{ return(adapter.dispatch('format_date', 'retail_intelligence')(col, fmt)) }}
{%- endmacro %}
{% macro default__format_date(col, fmt) -%} strftime({{ col }}, '{{ fmt }}') {%- endmacro %}
{% macro databricks__format_date(col, fmt) -%}
    date_format({{ col }}, '{{ fmt | replace('%Y', 'yyyy') | replace('%m', 'MM') | replace('%d', 'dd') | replace('%a', 'EEE') }}')
{%- endmacro %}
