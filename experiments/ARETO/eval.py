from tqdm import tqdm
from util import *

def extract_spoes(args,tokenizer,id2predicate,model,text, plabels,entity_start=0.5,entity_end=0.5,p_num=0.5):
    """抽取输入text所包含的三元组
    """
    #sigmoid=nn.Sigmoid()
    if isinstance(model,torch.nn.DataParallel):
        model=model.module
    model.to(args.device)
    tokens = tokenizer.tokenize(text, max_length=args.max_len)
    mapping = tokenizer.rematch(text, tokens)
    token_ids, _ ,mask = tokenizer.encode(text, max_length=args.max_len)

    #获取BERT表示
    model.eval()
    with torch.no_grad():
        sub, obj, rel, cls = model.get_embed(torch.tensor([token_ids]).to(args.device), torch.tensor([mask]).to(args.device),
                                             torch.tensor(plabels['ids']).to(args.device), torch.tensor(plabels['mask']).to(args.device))
        sub = sub.cpu().detach().numpy() #[1,L,H]
        obj = obj.cpu().detach().numpy()
        rel = rel.cpu().detach().numpy()
        cls = cls.cpu().detach().numpy()

    def get_entity(entity_pred):
        start = np.where(entity_pred[0, :, 0] > entity_start)[0]
        end = np.where(entity_pred[0, :, 1] > entity_end)[0]
        entity = []
        for i in start:
            j = end[end >= i]
            if len(j) > 0:
                j = j[0]
                entity.append((i, j))
        return entity

    #抽取s1 o1
    model.eval()
    with torch.no_grad():
        s1_preds = model.s_pred(torch.tensor(sub).to(args.device),torch.tensor(cls).to(args.device))

        s1_preds = s1_preds.cpu().detach().numpy() #[1,L,2]

        s1_preds[:,0,:],s1_preds[:,-1,:]=0.0,0.0

    s1=get_entity(s1_preds)

    pairs_1=[]
    for s in s1:
        #s:(start,end)
        s2_mask=np.zeros(len(token_ids)).astype(np.int64)
        s2_mask[s[0]] = 1
        s2_mask[s[1]] = 1

        model.eval()
        with torch.no_grad():
            o2_pred=model.o_pred_from_s(torch.tensor(sub).to(args.device),torch.tensor(obj).to(args.device),
                                       torch.tensor(s2_mask[None, :]).to(args.device), cls=torch.tensor(cls).to(args.device))
            o2_pred = o2_pred.cpu().detach().numpy()  # [1,L,2]
            o2_pred[:, 0, :], o2_pred[:, -1, :] = 0.0, 0.0
        objects2 = get_entity(o2_pred)
        if objects2:
            for o in objects2:
                pairs_1.append((s[0],s[1],o[0],o[1]))

    pairs_1=set(pairs_1)
    # pairs_2=set(pairs_2)

    pairs=list(pairs_1)

    # pairs=[(1,2,3,4),(3,4,5,6)]
    if pairs: # m * 4
        s_mask=np.zeros([len(pairs),len(token_ids)]).astype(np.int64)
        o_mask=np.zeros([len(pairs),len(token_ids)]).astype(np.int64)

        for i,pair in enumerate(pairs):
            s1, s2, o1, o2=pair
            s_mask[i,s1]=1
            s_mask[i,s2]=1
            o_mask[i,o1]=1
            o_mask[i,o2]=1

        spoes = []
        rel=np.repeat(rel,len(pairs),0)
        # 传入subject，抽取object和predicate
        model.eval()
        with torch.no_grad():
            p_pred = model.p_pred(
                                  rel=torch.tensor(rel).to(args.device),
                                  s_mask=torch.tensor(s_mask).to(args.device),
                                  o_mask=torch.tensor(o_mask).to(args.device),
                                  )
            p_pred = p_pred.cpu().detach().numpy() #BR

        index,p_index=np.where(p_pred>p_num)
        for i,p in zip(index,p_index):
            s1,s2,o1,o2=pairs[i]
            spoes.append(
                (
                 (mapping[s1][0],mapping[s2][-1]),
                  p,
                 (mapping[o1][0], mapping[o2][-1])
                )
            )

        return [(text[s[0]:s[1] + 1], id2predicate[str(p)], text[o[0]:o[1] + 1])
                for s, p, o, in spoes]
    else:
        return []


def getlabels(tokenizer,max_len, predicate2id):
    labels = list(predicate2id.keys())
    batch_labels_ids, batch_labels_mask = [], []
    for label in labels:
        label = label.replace('/', ' ').replace('_', ' ')
        token_ids, _, mask = tokenizer.encode(
            label, max_length=max_len
        )
        batch_labels_ids.append(token_ids)
        batch_labels_mask.append(mask)
    batch_labels_ids, batch_labels_mask = \
        [sequence_padding(i).astype(np.int64)
         for i in [batch_labels_ids, batch_labels_mask]]
    return {'ids':batch_labels_ids, 'mask':batch_labels_mask}


def evaluate(args,tokenizer,id2predicate, predicate2id, model,evl_data,evl_path):
    """评估函数，计算f1、precision、recall
    """
    X, Y, Z = 0, 0, 0
    f1, precision, recall = 0, 0, 0
    f = open(evl_path, 'w', encoding='utf-8')
    pbar = tqdm()
    plabels=getlabels(tokenizer,args.max_len,predicate2id)
    for d in evl_data:
        R = set(extract_spoes(args, tokenizer, id2predicate, model, d['text'],plabels))
        T = set([(i[0],i[1],i[2]) for i in d['triple_list']])
        X += len(R & T)
        Y += len(R)
        Z += len(T)
        f1 = 2 * X / (Y + Z) if Y + Z else 0.0
        precision = X / Y if Y else 0.0
        recall = X / Z if Z else 0.0
        pbar.update()
        pbar.set_description(
            'f1: %.5f, precision: %.5f, recall: %.5f' % (f1, precision, recall)
        )
        s = json.dumps({
            'source_id': d.get('source_id'),
            'window_id': d.get('window_id'),
            'text': d['text'],
            'triple_list': list(T),
            'triple_list_pred': list(R),
            'new': list(R - T),
            'lack': list(T - R),
        },ensure_ascii=False,indent=4)
        f.write(s + '\n')
    pbar.close()
    f.close()
    return f1, precision, recall
