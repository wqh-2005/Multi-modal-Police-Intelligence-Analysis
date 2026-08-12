"""快速冒烟测试：少量用例端到端验证 LLM 抽取链路与当前质量。

覆盖：手动用例 2 例 + 边界用例 5 例，输出 victim/suspect 与检查结果。
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.knowledge.extraction_service import run_extraction
from app.core.knowledge.storage_service import _infer_persons, _build_transactions

CASES = [
    {"id": "manual_r0", "text": "李先生在微信公众号阅读投资理财类文章后,扫码加入名为\"投资-特训营\"的群聊,并通过群内指引添加了自称\"基金经理\"的客服人员。该客服发送安装包要求李先生下载了指定的投资平台软件,并以\"股票推荐\"的名义诱导李先生向平台转账充值。初期账户持续显示盈利,面对账户余额不断增长,李先生放松警惕持续追加资金投入。"},
    {"id": "manual_r1", "text": "2022年11月16日，报警称被电信诈骗约12万元。经民警询问得知，2022年11月14日，报警人手机收到一条来自+这个号码的短信，内容为\"【顺丰】您好，由于您近期使用顺丰拿件次数较多，加支付宝好友登记，送您暖风扇1个！\"。后添加该支付宝账户为好友，并被对方拉入一支付宝的群聊中，组织者在群内发送了一个下载APP的链接，称可以用这个APP做任务，赚取佣金，便通过浏览器输入链接下载了一个叫\"全球口袋\"的手机APP，并注册登录了。进入APP以后，对方在APP里主动联系了做任务，并且把他拉入了一个群聊中。对方让先往APP里充值，然后跟群里的另外三个人一起做任务下注，下什么注、投多少积分都是按照对方的指示进行操作。先做了两个小任务，第一次给对方转账人民币99元，第二次给对方转账人民币499元。前两个任务都很顺利，当做到第三个任务的时候，再次给对方账户转账人民币999元，然后对方说操作错误任务失败了，如果要想继续完成任务并把之前做任务的钱领回，就要继续充值，否则任务终止，和其他人已经充值的钱就都不能返还了。就又按照对方的要求向对方转账了三笔钱，第一笔给对方转账人民币9880元，第二笔给对方转账人民币52800元，第三笔给对方转账人民币50000元。这时候对方还以交税等各种理由让继续转钱，就没有再向对方转账，最后他们说第二天能给返款，但是直到11月16日对方也没给返款，再次登录那个APP，发现群聊已经解散了，聊天记录也不见了，就觉得被骗了，然后就来派出所报警了。"},
    {"id": "edge_short1", "text": "报警人称被诈骗5000元。"},
    {"id": "edge_short2", "text": "受害人通过微信转账给骗子3000元，后发觉被骗。"},
    {"id": "edge_deepfake", "text": "对方通过视频通话冒充民警，要求转账到安全账户5万元。"},
    {"id": "edge_multiperson", "text": "张某、李某、王某三人合谋，通过虚假投资平台骗取赵某50万元。赵某报警后，警方将三人抓获。"},
]


async def main():
    total_ok = 0
    for case in CASES:
        t0 = time.time()
        try:
            extraction = await run_extraction(case["text"], case_id=case["id"])
            v, s = _infer_persons(extraction.triplets)
            tx = _build_transactions(extraction.triplets)
            elapsed = time.time() - t0
            ok = v.name != "未知" and s.name != "未知" and v.name != s.name
            total_ok += 1 if ok else 0
            print(f"[{case['id']}] {elapsed:.1f}s triplets={len(extraction.triplets)} "
                  f"victim={v.name!r} suspect={s.name!r} tx={len(tx)} {'✓' if ok else '✗'}")
            for t in extraction.triplets:
                print(f"    {t.subject}({t.subject_type}) -{t.relation}-> {t.object}({t.object_type})")
        except Exception as e:
            print(f"[{case['id']}] 错误: {e}")
    print(f"\n通过 {total_ok}/{len(CASES)}")


if __name__ == "__main__":
    asyncio.run(main())
