import torch
import torch.nn as nn
import torch.nn.functional as F

from transformer.Models import Encoder, Decoder
from transformer.Layers import PostNet
from modules import VarianceAdaptor
from utils import get_mask_from_lengths, Embedding, SpeakerIntegrator, get_speakers
import hparams as hp

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

class FastSpeech2(nn.Module):
    """ FastSpeech2 """

    # !! embedding을 위한 파라미터 추가
    def __init__(self, speaker_embed_dim=256, speaker_embed_std=0.01, use_postnet=True):
        super(FastSpeech2, self).__init__()

        # !! speaker embedding 추가
        self.n_speakers, self.speaker_table = get_speakers()
        self.speaker_embed_dim = speaker_embed_dim
        self.speaker_embed_std = speaker_embed_std

        # 싱글일 경우 임베딩 없이 학습 / 멀티일 경우 임베딩 적용 학습
        if len(self.speaker_table) == 1:
            self.single = True
            print('Now is Single')
        else:
            print('Now is Multi')
            self.single = False
        
            
        self.speaker_embeds = Embedding(self.n_speakers, speaker_embed_dim, padding_idx=0, std=speaker_embed_std)

        self.encoder = Encoder()

        # !! Speaker를 통합하는 레이어
        self.speaker_integrator = SpeakerIntegrator()

        self.variance_adaptor = VarianceAdaptor()

        self.decoder = Decoder()
        self.mel_linear = nn.Linear(hp.decoder_hidden, hp.n_mel_channels)
        
        self.use_postnet = use_postnet
        if self.use_postnet:
            self.postnet = PostNet()


    def forward(self, src_seq, src_len, speaker_ids=None, mel_len=None, d_target=None, p_target=None, e_target=None, max_src_len=None, max_mel_len=None, synthesize=False):
        src_mask = get_mask_from_lengths(src_len, max_src_len)
        mel_mask = get_mask_from_lengths(mel_len, max_mel_len) if mel_len is not None else None

        if self.single == True or synthesize == True: # Single Speaker (Fine-tune)
            # 임베딩 없이 학습 / single synthesize도 임베딩 없이 합성
            encoder_output = self.encoder(src_seq, src_mask)

        else: # Multi Spekaer (Pre-train)
            speaker_ids_dict = []
            if synthesize == True: # Multi 합성은 현재 합성 결과를 확인하기 위함
                speaker_ids_dict.append(list(self.speaker_table.values())[-1]) # !!!! 지금 너 필요 없어지려고 해
            else:
                for id in speaker_ids:
                    speaker_ids_dict.append(self.speaker_table[id])
            # 지정된 임베딩 값을 텐서로 변환하여 배치에 맞게 돌아가게 함 
            speaker_ids_dict = torch.tensor(speaker_ids_dict).long().to(device)
            # !! 스피커 임베딩 레이어 추가
            speaker_embed = self.speaker_embeds(speaker_ids_dict)
            encoder_output = self.encoder(src_seq, src_mask)
            # !! encoder output에 하나로 뭉친 output 추가
            encoder_output = self.speaker_integrator(encoder_output, speaker_embed)

        if d_target is not None:
            variance_adaptor_output, d_prediction, p_prediction, e_prediction, _, _ = self.variance_adaptor(
                encoder_output, src_mask, mel_mask, d_target, p_target, e_target, max_mel_len)
        else:
            variance_adaptor_output, d_prediction, p_prediction, e_prediction, mel_len, mel_mask = self.variance_adaptor(
                    encoder_output, src_mask, mel_mask, d_target, p_target, e_target, max_mel_len)
        decoder_output = self.decoder(variance_adaptor_output, mel_mask)
        mel_output = self.mel_linear(decoder_output)
        
        if self.use_postnet:
            mel_output_postnet = self.postnet(mel_output) + mel_output
        else:
            mel_output_postnet = mel_output

        return mel_output, mel_output_postnet, d_prediction, p_prediction, e_prediction, src_mask, mel_mask, mel_len


if __name__ == "__main__":
    # Test
    model = FastSpeech2(use_postnet=False)
    print(model)
    print(sum(param.numel() for param in model.parameters()))
