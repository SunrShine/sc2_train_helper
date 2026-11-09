import json
import sys

sys.path.insert(0, ".")
from replay_analyzer.flowtext import parse_sequence

PATH = "reports/tutorial-steps-pilot.json"

# (编号, bvid, build, 运营串, 思路要点, 已知时间点 {步序号: "m:ss"})
DATA = [
    (28, "BV1n34y1k7b5", "双气 5 死神 4 火车 1 运输机（死吗流，Maru 用过）",
     "BS兵营气矿气矿VF死神星轨BS VF双倍 火车BS VS 2死神火车2死神运输机",
     ["此开局可以克制 3 死神 2 火车压制",
      "3:58 装船，4:50 到脸：5 死神 4 火车 1 运输机操作",
      "渡鸦第二板斧杀主矿农民"],
     {}),
    (29, "BV15F411b7YU", "双气 3 死神 2 火车 枪兵 导弹车 运营",
     "BS兵营气矿气矿VF死神星轨BS火车死神BS VS死神火车，双科技",
     ["适合打实力比自己弱的、基本功差的人族；需熟练掌握侦查控图操作 3 要素",
      "1 船兵极限 4 分半到脸",
      "后续正常运营（前期可以在 3:55 偷 3 基地）",
      "注意侦查女妖"],
     {}),
    (30, "BV1bq4y147St", "双气 5 死神 3 火车（Maru IEM 用的开局）",
     "BS兵营气矿气矿VF死神星轨BS VF双倍 火车2死神BS火车VS2死神火车双科技",
     ["此开局比死吗流（28 期）TIMING 快 30 秒；4 分钟强换，后续导弹车渡鸦标准运营",
      "可有效克制对手 3 死神 2 火车开",
      "也可作为死吗流的 2 种变化，欺骗性开局"],
     {6: "1:31", 7: "1:31", 8: "1:31", 12: "2:11", 13: "2:11", 20: "3:40"}),
    (31, "BV1hL4y1u78c", "5 死神 3 火车 机械化（Bunny vs HeroMarine）",
     "BS兵营气矿气矿VF死神星轨BS VF双倍 火车BS VS2死神火车2死神(VF VS 双科技)",
     ["可克制 3 死神 2 火车压制",
      "可以不第一时间下 2VA（2VF 后）优先爆兵，防住对手一拳",
      "200 气 2VF 优先级：维京 > 坦克"],
     {}),
    (32, "BV1jb4y1W7Eq", "双气 3 死神 1 火车 导弹车 三渡鸦",
     "BS兵营气矿气矿VF死神星轨BS VF BS VS 3死神1火车1导弹车3渡鸦",
     ["3 死神 1 火车需要通过操作占据主动；如果操作失误，请出到 3 死神 2 火车",
      "2 死神第一时间侦查，1 死神第 2 时间侦查",
      "3 渡鸦可以配合维京提高中期推进能力"],
     {}),
    (33, "BV1eu411Q79k", "双气 2 死神 1 火车 1 雷 2 渡鸦 2 运输机",
     "BS兵营气矿气矿VF死神星轨BS VF BS VS 2死神1火车1雷2渡鸦2船兵",
     ["三板斧：1、2 渡鸦主矿二矿杀农民",
      "2、7 分钟 2 船兵到脸（二矿投下）",
      "3、保存渡鸦，有 4 运枪坦 2 渡鸦一拳 TIMING"],
     {1: "1:04", 3: "1:29"}),
    (34, "BV1tU4y1f7m7", "双气开 5 死神 3 火车 枪兵 坦克 运输机",
     "BS兵营气矿气矿死神星轨BS VF BS双倍 2火车后下VS 5死神3火车枪坦运双科技",
     ["4:00 5 死 3 火压 2 矿",
      "4:40 一船枪坦运高地压双倍挂件，可选择双线（吸引注意力杀二矿农民或正面）",
      "2 渡鸦出门压对手 3 基地（封视野）"],
     {}),
    (36, "BV1yL411A7N1", "双气开 三兵营 七死神 两运输机（Cure）",
     "BS兵营气矿兵营兵营气矿死神死神BS死神死神死神死神2基地双倍BS科技VF兴奋剂VS",
     ["前期注意封侦查！死神不要轻易送掉",
      "三板斧：1、3 分钟 4 个死神到脸",
      "2、6:30 2 船兵到脸",
      "3、4 船兵一拳"],
     {}),
    (38, "BV1ZT4y1m7cz", "双气 2 死神 1 火车 科技速导弹车 女妖",
     "BS兵营气矿气矿死神星轨BS VF死神二基地火车科技VS导弹车3气女妖双倍星轨导弹车",
     ["克制一船兵、5 死 3 火、火车 RUSH",
      "3 分半 2 死 1 火 1 导弹车可以防住 3 死 2 火等很多开局；女妖杀农民侦查",
      "更适合控图、占据主动"],
     {}),
    (42, "BV1tP4y1u7sR", "双气开 3 死神 2 火车 枪兵 导弹车",
     "BS兵营气矿气矿死神星轨BS 2基地VF VS 3死神2火车",
     ["3 死 2 火 3 分半到达对手 2 矿",
      "面对出 1 渡鸦不要强上了",
      "曾经流行的开局，比较怕 5 死 3 火等 RUSH"],
     {6: "1:04"}),
]


def main():
    data = json.load(open(PATH, encoding="utf-8"))
    by_number = {v["number"]: v for v in data["videos"]}
    updated = 0
    for number, bvid, build, sequence, plan, clocks in DATA:
        steps = parse_sequence(sequence)
        for index, clock in clocks.items():
            if 0 < index <= len(steps):
                steps[index - 1]["clock"] = clock
        entry = by_number.get(number, {"number": number, "bvid": bvid, "matchup": "TvT"})
        entry.update({
            "number": number,
            "bvid": bvid,
            "matchup": "TvT",
            "status": "complete",
            "build": build,
            "sequence_text": sequence,
            "steps": steps,
            "plan": plan,
            "note": "步骤来源：视频结尾「运营：」总结卡（作者手打完整建造串），已解析成结构化步骤；游戏时钟仅标出作者口播/画面明确给出的点。",
        })
        by_number[number] = entry
        updated += 1
    data["videos"] = sorted(by_number.values(), key=lambda v: v["number"])
    json.dump(data, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    total = sum(len(v.get("steps") or []) for v in data["videos"])
    print("更新 %d 条；步骤总数 %d" % (updated, total))
    for v in data["videos"]:
        print("  #%-3s %-9s %2d 步  %s" % (v["number"], v.get("status"), len(v.get("steps") or []),
                                           (v.get("sequence_text") or "")[:46]))


if __name__ == "__main__":
    main()
