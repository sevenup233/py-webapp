import orm,asyncio
from models import User, Blog, Comment

async def test(loop):

    await orm.create_pool(loop,user='www-data', password='www-data', db='awesome')
    u = User(name='root', email='root@qq.com', passwd='root', image='about:blank')
    await u.save()

loop = asyncio.get_event_loop()
loop.run_until_complete(test(loop))
loop.close()