from hoshino import Service

sv_help = '''
[谁是霸瞳] 角色别称查询
[霸瞳是谁] 同上，支持模糊匹配
'''.strip()

sv = Service('pcr-query', help_=sv_help, bundle='pcr查询')

from .whois import *
