import os
import json
import torch
import time
from transformers import BertConfig
from model import ARETO  # 替换为你的模型路径
from dataloader import data_generator  # 替换为你的数据加载逻辑
from bert4keras.tokenizers import Tokenizer  # 替换为你的分词器路径
from main import evaluate  # 替换为你的评估函数路径
# 定义测试函数
def test():
    # 设置参数
    class Args:
        cuda_id = "0"  # GPU ID
        max_len=100
        warmup=0.0
        weight_decay=0.0
        max_grad_norm=1.0
        min_num=1e-7
        bert_learning_rate=3e-5
        data_path = "./datasets"  # 数据路径
        dataset = "NYT_star"  # 数据集名称
        base_path = "./output"  # 输出路径
        file_id = "111"  # 实验文件ID
        bert_config_path = "D:\zryyan/bert-base-cased1/config.json"  # BERT配置文件路径
        bert_vocab_path = "D:\zryyan/bert-base-cased1/vocab.txt"  # BERT词汇表路径
        bert_model_path = "D:\zryyan/bert-base-cased1/pytorch_model.bin"  # BERT预训练模型路径



    args = Args()

    # 设置CUDA设备
    torch.cuda.set_device(int(args.cuda_id))

    # 路径设置
    test_path=r"D:\wucan\SRECode13\SRECode13\datasets\NYT_star\test.json"
   # test_path = os.path.join(args.data_path, args.dataset, "test1.json")
    output_path = os.path.join(args.base_path, args.dataset, args.file_id)
    output_path1 =r"D:\wucan\SRECode13\SRECode13\output\NYT_star\2"
    test_pred_path = os.path.join(output_path, "test_pred1.json")
    rel2id_path = os.path.join(args.data_path, args.dataset, "rel2id.json")
    log_path = os.path.join(output_path, "log.txt")

    # 加载测试数据和关系映射
    test_data = json.load(open(test_path, "r", encoding="utf-8"))
    id2predicate, predicate2id = json.load(open(rel2id_path, "r", encoding="utf-8"))

    # 加载模型配置和分词器
    config = BertConfig.from_pretrained(args.bert_config_path)
    tokenizer = Tokenizer(args.bert_vocab_path)
    config.num_p = len(id2predicate)

    # 加载模型
    train_model = ARETO.from_pretrained(
        pretrained_model_name_or_path=args.bert_model_path, config=config
    )
    train_model.to("cuda")
    train_model.load_state_dict(torch.load(os.path.join(output_path1, "pytorch_model.bin"), map_location="cuda"))

    # 记录推断开始时间
    start_time = time.time()
    f1, precision, recall = evaluate(args, tokenizer, id2predicate, predicate2id, train_model, test_data,
                                     test_pred_path)
    # 记录推断结束时间
    end_time = time.time()

    # 计算每个句子的推断时间（单位：毫秒）
    inference_time_ms = (end_time - start_time) * 1000 / len(test_data)  # 每个句子的推断时间

    print("f1:%f, precision:%f, recall:%f" % (f1, precision, recall))
    #
    print("每个句子的推断时间：%f ms" % inference_time_ms)



# 主程序入口
if __name__ == "__main__":
    test()


