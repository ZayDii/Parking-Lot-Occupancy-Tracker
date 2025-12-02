import os, psycopg2
url = os.environ["DATABASE_URL"]
with psycopg2.connect(url) as conn:
    with conn.cursor() as cur:
        cur.execute("UPDATE public.lots SET total_spaces = %s WHERE id = %s", (77, "96N"))
        cur.execute("SELECT id, name, total_spaces FROM public.lots WHERE id = %s", ("96N",))
        print(cur.fetchone())  # expect ('96N', 'Lot 96N', 77)
    conn.commit()
