"""Entry points for imports that read real production data.

Everything under this package touches a genuine upstream source: a CMP production
export, an event registration archive, a course repository.  Nothing that invents rows
belongs here, and nothing here may import a seeder -- the split is by *what data a
module touches*, so a reader can tell from the path whether a script is safe to point
at a scratch database.
"""
