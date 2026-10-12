import torch

from util import *


def search(pattern, sequence):
    """从sequence中寻找子串pattern
    如果找到，返回第一个下标；否则返回-1。
    """
    n = len(pattern)
    for i in range(len(sequence)):
        if sequence[i:i + n] == pattern:
            return i
    return -1


class data_generator(DataGenerator):
    """数据生成器
    """
    def __init__(self, args, train_data, tokenizer, predicate2id, id2predicate):
        super(data_generator, self).__init__(train_data, args.batch_size)
        self.max_len=args.max_len
        self.tokenizer=tokenizer
        self.predicate2id=predicate2id
        self.id2predicate=id2predicate

    def __iter__(self, is_random=True):
        batch_token_ids, batch_mask = [], []
        batch_s1_labels, batch_s2_mask, batch_o2_labels,\
            batch_s3_mask, batch_o3_mask, batch_r = [],[],[],[],[],[]
        labels = list(self.predicate2id.keys())
        batch_labels_ids, batch_labels_mask = [], []
        for label in labels:
            label = label.replace('/', ' ').replace('_', ' ')
            token_ids, _, mask = self.tokenizer.encode(
                label, max_length=self.max_len
            )
            batch_labels_ids.append(token_ids)
            batch_labels_mask.append(mask)
        batch_labels_ids, batch_labels_mask = \
        [sequence_padding(i).astype(np.int64)
         for i in [ batch_labels_ids, batch_labels_mask]]

        for is_end, d in self.sample(is_random):
            if judge(d)==False:
                continue
            token_ids, _ ,mask = self.tokenizer.encode(
                d['text'], max_length=self.max_len
            )
            # 整理三元组 {s: [(o, p)]}
            spoes_s = {}
            for s, p, o in d['triple_list']:
                s = self.tokenizer.encode(s)[0][1:-1]
                p = self.predicate2id[p]
                o = self.tokenizer.encode(o)[0][1:-1]
                s_idx = search(s, token_ids)
                o_idx = search(o, token_ids)
                if s_idx != -1 and o_idx != -1:
                    s_loc = (s_idx, s_idx + len(s) - 1)
                    o_loc = (o_idx, o_idx + len(o) - 1)
                    if s_loc not in spoes_s:
                        spoes_s[s_loc] = []
                    spoes_s[s_loc].append((o_loc,p))

            if spoes_s: #and spoes_o:
                # s1_labels o1_labels
                def get_entity1_labels(item,l):
                    res=np.zeros([l,2])
                    for start,end in item:
                        res[start][0]=1
                        res[end][1]=1
                    return res
                s1_labels = get_entity1_labels(spoes_s, len(token_ids))

                def get_entity2_labels_mask(item,l):
                    start, end = random.choice(list(item.keys()))
                    #构造labels
                    labels = np.zeros((l, 2))
                    if (start,end) in item:
                        for loc,_ in item[(start,end)]:
                            labels[loc[0], 0] = 1
                            labels[loc[1], 1] = 1
                    #构造mask
                    mask=np.zeros(l)
                    mask[start]=1
                    mask[end]=1
                    return labels,mask
                o2_labels,s2_mask = get_entity2_labels_mask(spoes_s,len(token_ids))

                s_loc=random.choice(list(spoes_s.keys()))
                o_loc,_=random.choice(spoes_s[s_loc])

                r=np.zeros(len(self.id2predicate))
                if s_loc in spoes_s:
                    for loc,the_r in spoes_s[s_loc]:
                        if loc==o_loc:
                            r[the_r]=1
                s3_mask=np.zeros(len(token_ids))
                o3_mask=np.zeros(len(token_ids))
                s3_mask[s_loc[0]]=1
                s3_mask[s_loc[1]]=1
                o3_mask[o_loc[0]]=1
                o3_mask[o_loc[1]]=1

                # 构建batch
                batch_token_ids.append(token_ids)
                batch_mask.append(mask)

                batch_s1_labels.append(s1_labels)


                batch_s2_mask.append(s2_mask)

                batch_o2_labels.append(o2_labels)

                batch_s3_mask.append(s3_mask)
                batch_o3_mask.append(o3_mask)
                batch_r.append(r)

                if len(batch_token_ids) == self.batch_size or is_end:   #输出batch
                    batch_token_ids,batch_mask,\
                    batch_s1_labels,\
                    batch_s2_mask,batch_o2_labels,\
                    batch_s3_mask,batch_o3_mask=\
                        [sequence_padding(i).astype(np.int64)
                         for i in [batch_token_ids,batch_mask,
                                   batch_s1_labels,
                                   batch_s2_mask,batch_o2_labels,
                                   batch_s3_mask,batch_o3_mask]]

                    batch_r = np.array(batch_r).astype(np.int64)

                    yield [
                        batch_token_ids, batch_mask,batch_labels_ids, batch_labels_mask,
                        batch_s1_labels,
                        batch_s2_mask, batch_o2_labels,
                        batch_s3_mask, batch_o3_mask,batch_r
                    ]
                    batch_token_ids, batch_mask = [], []
                    batch_s1_labels, \
                    batch_s2_mask, batch_o2_labels, \
                    batch_s3_mask, batch_o3_mask, batch_r = [], [], [], [], [], []

        # A skipped last record must not discard an already accumulated batch.
        if len(batch_token_ids):
            padded = [sequence_padding(i).astype(np.int64) for i in [
                batch_token_ids, batch_mask, batch_s1_labels, batch_s2_mask,
                batch_o2_labels, batch_s3_mask, batch_o3_mask,
            ]]
            ids, masks, subjects, subject_masks, objects, s_masks, o_masks = padded
            yield [ids, masks, batch_labels_ids, batch_labels_mask,
                   subjects, subject_masks, objects, s_masks, o_masks,
                   np.asarray(batch_r, dtype=np.int64)]
