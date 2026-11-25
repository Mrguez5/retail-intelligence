{# '1,234.56 EUR' (English layout) or '1.234,56 €' (Spanish layout) -> decimal #}
{% macro parse_eur(col) -%}
    case
        when {{ col }} like '%€%'
            then try_cast(replace(replace(trim(replace({{ col }}, '€', '')), '.', ''), ',', '.') as decimal(12, 2))
        else try_cast(replace(trim(replace({{ col }}, 'EUR', '')), ',', '') as decimal(12, 2))
    end
{%- endmacro %}

{# decimal comma or decimal point -> decimal #}
{% macro parse_number(col) -%}
    try_cast(replace(trim({{ col }}), ',', '.') as decimal(12, 2))
{%- endmacro %}

{# key used to match titles typed by people: no case, accents, punctuation or extra spaces #}
{% macro title_key(col) -%}
    trim({{ regexp_replace_all(strip_accents_lower('trim(' ~ col ~ ')'), '[^a-z0-9]+', ' ') }})
{%- endmacro %}
