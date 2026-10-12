from transformers import BertModel,BertPreTrainedModel
import torch.nn as nn
import torch
import torch.nn.functional as F
import math

class ARETO(BertPreTrainedModel):
    def __init__(self, config):
        super(ARETO, self).__init__(config)
        self.config = config
        self.bert=BertModel(config=config)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)

        self.w1=nn.Linear(config.hidden_size,config.hidden_size)
        self.w2=nn.Linear(config.hidden_size,config.hidden_size)
        self.w3=nn.Linear(config.hidden_size,config.hidden_size)

        self.layers = GATLayer(config.hidden_size)

        #s
        self.s_classier=nn.Linear(config.hidden_size,2)

        #o
        self.o_classier_from_s=nn.Linear(config.hidden_size,2)

        #p
        self.rel_pre = nn.Linear(config.hidden_size * 2,config.num_p)

        self.sigmoid=nn.Sigmoid()
        self.init_weights()

    def forward(self, token_ids, mask_token_ids, batch_labels_ids, batch_labels_mask, s2_mask, s3_mask, o3_mask):
        '''
        :param token_ids:
        :param token_type_ids:
        :param mask_token_ids:
        :param s_loc:
        :return: s_pred: [batch,seq,2]
        op_pred: [batch,seq,p,2]
        '''

        # 获取表示
        sub, obj, rel, cls = self.get_embed(token_ids, mask_token_ids, batch_labels_ids, batch_labels_mask)
        # 初步预测s o
        s1_pred = self.s_pred(sub,cls=cls)

        # 进一步预测 s,o
        o2_pred = self.o_pred_from_s(sub, obj, s2_mask,cls)

        # 预测r
        p_pred = self.p_pred(rel,s3_mask,o3_mask)

        return s1_pred, o2_pred, p_pred

    def get_embed(self,token_ids, mask_token_ids, batch_labels_ids, batch_labels_mask):
        bert_out = self.bert(input_ids=token_ids.long(), attention_mask=mask_token_ids.long())
        embed=bert_out[0]

        labels_embed = self.bert(input_ids=batch_labels_ids.long(), attention_mask=batch_labels_mask.long())[0]

        labels_embed = labels_embed * batch_labels_mask.unsqueeze(dim=-1)
        labels_embed = torch.sum(labels_embed, dim=1, keepdim=False)

        labels_embed = labels_embed.unsqueeze(0).expand(embed.size(0), labels_embed.size(0), labels_embed.size(1))

        embed = self.layers(embed, labels_embed)

        sub = self.w1(embed)
        obj = self.w2(embed)
        rel = self.w3(embed)
        cls = bert_out[1]

        sub, obj, rel, cls = self.dropout(sub),self.dropout(obj),self.dropout(rel),self.dropout(cls)
        return sub, obj, rel, cls

    def extract_entity(self, input, mask):
        '''
        取首尾平均
        :param input:BLH
        :param mask:BL
        :return: BH
        '''
        _,_,dim=input.shape
        entity=input*mask.unsqueeze(dim=-1)
        entity=entity.sum(dim=1)/mask.sum(dim=-1,keepdim=True)
        return entity

    def s_pred(self,sub,cls):
        s_logist = self.s_classier(sub+cls.unsqueeze(dim=1))
        s_pred=self.sigmoid(s_logist)
        return s_pred

    def o_pred_from_s(self,sub,obj,s_mask, cls):
        s_entity = self.extract_entity(sub, s_mask)
        s2o_embed = obj*s_entity.unsqueeze(dim=1)
        o_logist = self.o_classier_from_s(s2o_embed+cls.unsqueeze(dim=1))
        o_pred = self.sigmoid(o_logist)
        return o_pred

    def p_pred(self, rel, s_mask, o_mask):
        s_entity = self.extract_entity(rel,s_mask)
        o_entity = self.extract_entity(rel,o_mask)

        logist = self.rel_pre(torch.cat([s_entity, o_entity], dim=-1))
        r_pred = self.sigmoid(logist)
        return r_pred


class GATLayer(nn.Module):

    def __init__(self, hidden_size):
        super().__init__()
        self.ra = DotProductAttention(hidden_size, 0.1)
        self.gate = nn.Linear(hidden_size * 2, 1)

    def forward(self, x, p):
        out = self.ra(x, p, p) + x
        g = self.gate(torch.cat([out, x], 2)).sigmoid()
        out = g * out + (1 - g) * x
        x = out + x
        return x


class DotProductAttention(nn.Module):
    def __init__(self, hidden_size, dropout):
        super(DotProductAttention, self).__init__()
        self.dropout = nn.Dropout(dropout)
        self.Q = nn.Linear(hidden_size,hidden_size, bias=False)
        self.K = nn.Linear(hidden_size,hidden_size, bias=False)

    def forward(self, queries, keys, values):
        '''
        queries --> [batch_size, queries_length, queries_feature_num]
        keys --> [batch_size, keys_values_length, keys_features_num]
        values --> [barch_size, keys_values_length, values_features_num]
        点积模型中： queries_features_num = keys_features_num
        '''
        queries = self.Q(queries)
        keys = self.K(keys)
        d = queries.shape[-1]
        scores = torch.bmm(queries, keys.transpose(1, 2)) / math.sqrt(d)
        attention_weights = F.softmax(scores, dim=1)
        return torch.bmm(self.dropout(attention_weights), values)