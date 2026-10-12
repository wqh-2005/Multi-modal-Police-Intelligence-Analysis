import os
from util import *
import json
from transformers import WEIGHTS_NAME, get_linear_schedule_with_warmup, BertConfig
from torch.optim import AdamW
from tokenizer import Tokenizer
from tqdm import tqdm
from dataloader import data_generator
from model import ARETO
import torch.nn as nn
from eval import evaluate
import time
class CE():
    def __call__(self,args,targets, pred, from_logist=False):
        '''
        计算二分类交叉熵dataloader
        :param targets: [batch,seq,2]
        :param pred: [batch,seq,2]
        :param from_logist:是否没有经过softmax/sigmoid
        :return: loss.shape==targets.shape==pred.shape
        '''
        if not from_logist:
            '''返回到没有经过softmax/sigmoid得张量'''
            # 截取pred，防止趋近于0或1,保持在[min_num,1-min_num]
            pred = torch.where(pred < 1 - args.min_num, pred, torch.ones(pred.shape).to(args.device) * 1 - args.min_num).to(args.device)
            pred = torch.where(pred > args.min_num, pred, torch.ones(pred.shape).to(args.device) * args.min_num).to(args.device)
            pred = torch.log(pred / (1 - pred))
        relu = nn.ReLU()
        # 计算传统的交叉熵loss
        loss = relu(pred) - pred * targets + torch.log(1 + torch.exp(-1 * torch.abs(pred).to(args.device))).to(args.device)
        return loss


def train(args):
    output_path = os.path.join(args.base_path, args.dataset, args.file_id)
    train_path=os.path.join(args.data_path,args.dataset,"train.json")
    dev_path=os.path.join(args.data_path,args.dataset,"dev.json")
    test_path=os.path.join(args.data_path,args.dataset,"test.json")
    rel2id_path=os.path.join(args.data_path,args.dataset,"rel2id.json")
    test_pred_path=os.path.join(output_path,"test_pred.json")
    dev_pred_path=os.path.join(output_path,"dev_pred.json")
    log_path=os.path.join(output_path,"log.txt")

    if not os.path.exists(output_path):
        os.makedirs(output_path)
    print_config(args)
    # 加载数据集
    train_data = json.load(open(train_path,encoding='utf-8'))
    #train_data = train_data[:100]
    valid_data = json.load(open(dev_path,encoding='utf-8'))
    #valid_data = valid_data[:100]
    test_data = json.load(open(test_path,encoding='utf-8'))
    if args.train_samples:
        train_data = train_data[:args.train_samples]
    if args.eval_samples:
        valid_data = valid_data[:args.eval_samples]
        test_data = test_data[:args.eval_samples]
    print(f'Dataset={args.dataset}, train={len(train_data)}, dev={len(valid_data)}, test={len(test_data)}, device={args.device}', flush=True)
    #test_data = test_data[4:5]
    id2predicate, predicate2id = json.load(open(rel2id_path,encoding='utf-8'))

    tokenizer = Tokenizer(args.bert_vocab_path)  # 注意修改
    config = BertConfig.from_pretrained(args.bert_config_path)
    config.num_p=len(id2predicate)
    if args.device == 'cuda':
        torch.cuda.set_device(int(args.cuda_id))
    train_model = ARETO.from_pretrained(pretrained_model_name_or_path=args.bert_model_path, config=config)
    train_model.to(args.device)

    if not os.path.exists(output_path):
        os.makedirs(output_path)

    dataloader = data_generator(args, train_data, tokenizer, predicate2id, id2predicate)

    t_total = len(dataloader) * args.num_train_epochs

    """ 优化器准备 """
    optimizer_grouped_parameters = [
        {
            "params": [p for n, p in train_model.named_parameters() if "bert." in n],
            "weight_decay": args.weight_decay,
            "lr": args.bert_learning_rate,
        },
        {
            "params": [p for n, p in train_model.named_parameters() if "bert." not in n],
            "weight_decay": args.weight_decay,
            "lr": args.other_learning_rate,
        }
    ]

    optimizer = AdamW(optimizer_grouped_parameters, eps=args.min_num)
    scheduler = get_linear_schedule_with_warmup(
        optimizer, num_warmup_steps=args.warmup * t_total, num_training_steps=t_total
    )
    best_f1 = -1.0  # 全局的best_f1
    step = 0
    binary_crossentropy=CE()
    no_change=0
    stop=0
    for epoch in range(args.num_train_epochs):
        train_model.train()
        epoch_loss = 0
        epoch_batches = 0

        #新加入的一行
        epoch_start_time = time.time()  # 记录当前epoch的开始时间epoch_start_time = time.time()  # 记录当前epoch的开始时间

        with tqdm(total=dataloader.__len__(), desc="train", ncols=80) as t:
            for i, batch in enumerate(dataloader):
                epoch_batches += 1
                batch = [torch.tensor(d).to(args.device) for d in batch]
                # batch_token_ids, batch_mask,batch_labels, batch_labels_mask,\
                # batch_s1_labels, batch_o1_labels,\
                # batch_s2_mask, batch_o2_mask, batch_s2_labels, batch_o2_labels,\
                # batch_s3_mask, batch_o3_mask, batch_r = batch
                batch_token_ids, batch_mask, batch_labels, batch_labels_mask, \
                batch_s1_labels, batch_s2_mask, batch_o2_labels, \
                batch_s3_mask, batch_o3_mask, batch_r = batch
                s1_pred,o2_pred,p_pred = train_model(batch_token_ids, batch_mask,
                                                                     batch_labels, batch_labels_mask,
                                                                     batch_s2_mask, batch_s3_mask, batch_o3_mask)

                #计算损失
                def get_loss(target,pred,mask):
                    loss = binary_crossentropy(args, targets=target, pred=pred)  # BL2
                    loss = torch.mean(loss, dim=2).to(args.device)  # BL
                    loss = torch.sum(loss * mask).to(args.device) / torch.sum(mask).to(args.device)
                    return loss
                s1_loss=get_loss(target=batch_s1_labels,pred=s1_pred,mask=batch_mask)
                # o1_loss=get_loss(target=batch_o1_labels,pred=o1_pred,mask=batch_mask)
                # s2_loss=get_loss(target=batch_s2_labels,pred=s2_pred,mask=batch_mask)
                o2_loss=get_loss(target=batch_o2_labels,pred=o2_pred,mask=batch_mask)
                r_loss=binary_crossentropy(args,targets=batch_r,pred=p_pred)
                r_loss=r_loss.mean()

                loss=s1_loss+o2_loss+r_loss

                loss.backward()
                step += 1
                epoch_loss += loss.item()
                torch.nn.utils.clip_grad_norm_(train_model.parameters(), args.max_grad_norm)
                optimizer.step()
                scheduler.step()  # Update learning rate schedule
                train_model.zero_grad()
                t.set_postfix(loss="%.4lf"%(loss.cpu().item()))
                t.update(1)

         # 计算当前epoch的耗时并显示
        epoch_end_time = time.time()
        epoch_time = epoch_end_time - epoch_start_time  # 计算时间差
        print(f"Epoch {epoch + 1} finished in {epoch_time:.2f} seconds")  # 输出每个epoch的耗时

        f1, precision, recall = evaluate(args,tokenizer,id2predicate,predicate2id,train_model,valid_data,dev_pred_path)

        if f1 > best_f1:
            # Save model checkpoint
            best_f1 = f1
            torch.save(train_model.state_dict(), os.path.join(output_path, WEIGHTS_NAME))  # 保存最优模型权重

        if epoch_batches == 0:
            raise RuntimeError('No usable training batches; check entity spans and max_len')
        epoch_loss = epoch_loss / epoch_batches
        with open(log_path, "a", encoding="utf-8") as f:
            now = time.strftime("%Y-%m-%d-%H:%M:%S", time.localtime())
            f.write("%s\tepoch:%d\tloss:%f\tf1:%f\tprecision:%f\trecall:%f\tbest_f1:%f\n" % (
                now,int(epoch), epoch_loss, f1,precision, recall,best_f1))
    #加载训练好的权重
    train_model.load_state_dict(torch.load(os.path.join(output_path, WEIGHTS_NAME), map_location=args.device))
    f1, precision, recall = evaluate(args,tokenizer,id2predicate,predicate2id, train_model, test_data, test_pred_path)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("finsh, test: f1:%f\tprecision:%f\trecall:%f\n" % (f1, precision, recall))


def test(args):
    if args.device == 'cuda':
        torch.cuda.set_device(int(args.cuda_id))
    test_path = os.path.join(args.data_path, args.dataset, "test.json")
    output_path = os.path.join(args.base_path,args.dataset, args.file_id)
    test_pred_path = os.path.join(output_path, "test_pred.json")
    rel2id_path=os.path.join(args.data_path,args.dataset, "rel2id.json")
    test_data = json.load(open(test_path, encoding='utf-8'))
    if args.eval_samples:
        test_data = test_data[:args.eval_samples]
    id2predicate, predicate2id = json.load(open(rel2id_path, encoding='utf-8'))
    config = BertConfig.from_pretrained(args.bert_config_path)
    tokenizer = Tokenizer(args.bert_vocab_path)
    config.num_p=len(id2predicate)
    train_model = ARETO.from_pretrained(pretrained_model_name_or_path=args.bert_model_path,config=config)
    train_model.to(args.device)

    train_model.load_state_dict(torch.load(os.path.join(output_path, WEIGHTS_NAME), map_location=args.device))

    #
    # 记录推断开始时间
    start_time = time.time()
    #新加的

    f1, precision, recall = evaluate(args,tokenizer,id2predicate,predicate2id,train_model, test_data, test_pred_path)
    # 记录推断结束时间
    end_time = time.time()
    #新加的--可刪除

    # 计算每个句子的推断时间（单位：毫秒）
    inference_time_ms = (end_time - start_time) * 1000 / len(test_data)  # 每个句子的推断时间
    # #新加的--可刪除

    print("f1:%f, precision:%f, recall:%f"%(f1, precision, recall))
    #
    print("每个句子的推断时间：%f ms" % inference_time_ms)
    # #新加的--可刪除
