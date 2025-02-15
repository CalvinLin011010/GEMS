
import torch.nn as nn
import torch
from transformers.models.t5.modeling_t5 import *
from transformers.file_utils import ModelOutput
from transformers.generation_utils import *
from transformers.generation_beam_search import *
import copy


_CONFIG_FOR_DOC = "T5Config"

_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


class CrossMultiAttention(nn.Module):  
    def __init__(self, dim, num_heads=8, attn_drop=0.2, proj_drop=0.2, qkv_bias=False, qk_scale=None):
        super().__init__()

        _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        self.num_heads = num_heads
        head_dim = dim // num_heads
        self.scale = qk_scale or head_dim ** -0.5

        self.wq = nn.Linear(dim, dim, bias=qkv_bias).to(_device)
        self.wk = nn.Linear(dim, dim, bias=qkv_bias).to(_device)
        self.wv = nn.Linear(dim, dim, bias=qkv_bias).to(_device)
        self.attn_drop = nn.Dropout(attn_drop)
        self.proj = nn.Linear(dim, dim)
        self.proj_drop = nn.Dropout(proj_drop)
        self.addnorm = AddNorm([1, dim])

    def forward(self, y, x_cls):

        y_all = torch.concat((y, x_cls), dim=1)   
        B, N, C = y_all.shape   

        q = self.wq(x_cls).reshape(B, 1, self.num_heads, C // self.num_heads).permute(0, 2, 1, 3)  
        k = self.wk(y_all).reshape(B, N, self.num_heads, C // self.num_heads).permute(0, 2, 1, 3)
        v = self.wv(y_all).reshape(B, N, self.num_heads, C // self.num_heads).permute(0, 2, 1, 3)

        attn = (q @ k.transpose(-2, -1)) * self.scale   
        attn = attn.softmax(dim=-1)



        attn = self.attn_drop(attn)

        x = (attn @ v).transpose(1, 2).reshape(B, 1, C)  
        x = self.proj(x)
        x = self.proj_drop(x)
        o = x + x_cls  

        return self.addnorm(o)


class AddNorm(nn.Module):

    def __init__(self, normalized_shape, **kwargs):
        super(AddNorm, self).__init__(**kwargs)
        self.ln = nn.LayerNorm(normalized_shape)

    def forward(self, Y):
        return self.ln(Y)



add_start_docstrings("""T5 Model with a `language modeling` head on top. """, T5_START_DOCSTRING)
@add_start_docstrings("""T5 Model with a `language modeling` head on top. """, T5_START_DOCSTRING)  
class Gems(T5PreTrainedModel):
    authorized_missing_keys = [r"encoder\.embed_tokens\.weight", r"decoder\.embed_tokens\.weight", r"lm_head\.weight"] 

    def __init__(self, config, head):
        super().__init__(config)
        self.model_dim = config.d_model

        self.shared = nn.Embedding(config.vocab_size, config.d_model).to(_device)

        encoder_config = copy.deepcopy(config)
        encoder_config.use_cache = False
        encoder_config.is_encoder_decoder = False
        self.encoder = T5Stack(encoder_config, self.shared).to(_device)

        self.linear_event = nn.Linear(100, 1, bias=False)  
        self.linear_sent = nn.Linear(250, 1, bias=False)  
        self.cross_attention_event = CrossMultiAttention(config.d_model, head)  


        decoder_config = copy.deepcopy(config)
        decoder_config.is_decoder = True
        decoder_config.is_encoder_decoder = False
        decoder_config.num_layers = config.num_decoder_layers
        self.decoder = T5Stack(decoder_config, self.shared).to(_device)

        self.lm_head = nn.Linear(config.d_model, config.vocab_size, bias=False).to(_device)


        self.init_weights()

    def get_input_embeddings(self):
        return self.shared

    def set_input_embeddings(self, new_embeddings):
        self.shared = new_embeddings.to(_device)
        self.encoder.set_input_embeddings(new_embeddings).to(_device)
        self.decoder.set_input_embeddings(new_embeddings).to(_device)

    def get_output_embeddings(self):
        return self.lm_head

    def get_encoder(self):
        return self.encoder

    def get_decoder(self):
        return self.decoder

    @add_start_docstrings_to_model_forward(T5_INPUTS_DOCSTRING)
    @replace_return_docstrings(output_type=Seq2SeqLMOutput, config_class=_CONFIG_FOR_DOC)
    def forward(
        self,
        input_ids=None,
        attention_mask=None,   
        decoder_input_ids=None,
        decoder_attention_mask=None,
        encoder_outputs=None,
        past_key_values=None,
        head_mask=None,
        inputs_embeds=None,
        decoder_inputs_embeds=None,
        labels=None,
        use_cache=None,
        output_attentions=None,
        output_hidden_states=None,
        return_dict=None,
        event_description_ids=None,  
        event_description_mask=None,  
        encoder_outputs_event_description=None,
        cross_attn_cls=None,
        **kwargs,
    ):


        if "lm_labels" in kwargs:
            warnings.warn(
                "The `lm_labels` argument is deprecated and will be removed in a future version, use `labels` instead.",
                FutureWarning,
            )
            labels = kwargs.pop("lm_labels")
        if "decoder_past_key_value_states" in kwargs:
            warnings.warn(
                "The `decoder_past_key_value_states` argument is deprecated and will be removed in a future version, use `past_key_values` instead.",
                FutureWarning,
            )
            past_key_values = kwargs.pop("decoder_past_key_value_states")
        if "decoder_past_key_values" in kwargs:
            warnings.warn(
                "The `decoder_past_key_values` argument is deprecated and will be removed in a future version, use `past_key_values` instead.",
                FutureWarning,
            )
            past_key_values = kwargs.pop("decoder_past_key_values")
        assert kwargs == {}, f"Unexpected keyword arguments: {list(kwargs.keys())}."

        use_cache = use_cache if use_cache is not None else self.config.use_cache
        return_dict = return_dict if return_dict is not None else self.config.use_return_dict


        if encoder_outputs_event_description is None:

            if event_description_ids is not None and event_description_mask is not None:
                encoder_outputs_event_description = self.encoder(
                    input_ids=event_description_ids,  
                    attention_mask=event_description_mask,  
                    inputs_embeds=None,  
                    head_mask=None,  
                    output_attentions=output_attentions,  
                    output_hidden_states=output_hidden_states,  
                    return_dict=return_dict,  
                )  


        if encoder_outputs is None:

            encoder_outputs = self.encoder(
                input_ids=input_ids,  
                attention_mask=attention_mask,  
                inputs_embeds=inputs_embeds,  
                head_mask=head_mask,  
                output_attentions=output_attentions,  
                output_hidden_states=output_hidden_states,  
                return_dict=return_dict,  
            )
        elif return_dict and not isinstance(encoder_outputs, BaseModelOutput):
            encoder_outputs = BaseModelOutput(
                last_hidden_state=encoder_outputs[0],
                hidden_states=encoder_outputs[1] if len(encoder_outputs) > 1 else None,
                attentions=encoder_outputs[2] if len(encoder_outputs) > 2 else None,
            )


        hidden_states = encoder_outputs[0]  

        if cross_attn_cls is None:

            hidden_states_event = encoder_outputs_event_description["last_hidden_state"]
            hidden_states_event_convert = hidden_states_event.transpose(1, 2)  
            hidden_states_convert = hidden_states.transpose(1, 2)  
            cls_event = self.linear_event(hidden_states_event_convert).transpose(2, 1)  
            cls_sent = self.linear_sent(hidden_states_convert).transpose(2, 1)  
            cross_attn_cls = self.cross_attention_event(hidden_states, cls_event) + self.cross_attention_event(hidden_states_event, cls_sent) 



        if labels is not None and decoder_input_ids is None and decoder_inputs_embeds is None:

            decoder_input_ids = self._shift_right(labels)  


        if past_key_values is not None:  
            assert labels is None, "Decoder should not use cached key value states when training."
            if decoder_input_ids is not None:
                decoder_input_ids = decoder_input_ids[:, -1:]  
            if decoder_inputs_embeds is not None:
                decoder_inputs_embeds = decoder_inputs_embeds[:, -1:]

        decoder_outputs = self.decoder(
            input_ids=decoder_input_ids,  
            attention_mask=decoder_attention_mask,  
            inputs_embeds=decoder_inputs_embeds,  
            past_key_values=past_key_values,  
            encoder_hidden_states=hidden_states,  
            encoder_attention_mask=attention_mask,  
            head_mask=head_mask,  
            use_cache=use_cache,  
            output_attentions=output_attentions,  
            output_hidden_states=output_hidden_states,  
            return_dict=return_dict,  
        )

        ## steer
        sequence_output = decoder_outputs[0] + cross_attn_cls 



        sequence_output = sequence_output * (self.model_dim ** -0.5)
        lm_logits = self.lm_head(sequence_output)  

        loss = None
        if labels is not None:
            loss_fct = CrossEntropyLoss(ignore_index=-100)
            loss = loss_fct(lm_logits.perspective(-1, lm_logits.size(-1)), labels.perspective(-1))


        if not return_dict:
            output = (lm_logits,) + decoder_outputs[1:] + encoder_outputs
            return ((loss,) + output) if loss is not None else output

        return Seq2SeqLMOutput(  
            loss=loss,  
            logits=lm_logits,  
            past_key_values=decoder_outputs.past_key_values,  
            decoder_hidden_states=decoder_outputs.hidden_states,   
            decoder_attentions=decoder_outputs.attentions,  
            cross_attentions=decoder_outputs.cross_attentions,  
            encoder_last_hidden_state=encoder_outputs.last_hidden_state,  
            encoder_hidden_states=encoder_outputs.hidden_states,  
            encoder_attentions=encoder_outputs.attentions,  
        )

    def prepare_inputs_for_generation(
        self, input_ids, past=None, attention_mask=None, use_cache=None, encoder_outputs=None, 
        event_description_ids=None, event_description_mask=None, encoder_outputs_event_description=None, cross_attn_cls=None, **kwargs
    ):


        if past is not None:
            input_ids = input_ids[:, -1:]

        return {
            "decoder_input_ids": input_ids,
            "past_key_values": past,
            "encoder_outputs": encoder_outputs,
            "attention_mask": attention_mask,
            "use_cache": use_cache,
            "event_description_ids": event_description_ids,
            "event_description_mask": event_description_mask,
            "encoder_outputs_event_description": encoder_outputs_event_description,
            "cross_attn_cls": cross_attn_cls
        }

    def _reorder_cache(self, past, beam_idx):


        if past is None:
            logger.warning("You might want to consider setting `use_cache=True` to speed up decoding")
            return past

        reordered_decoder_past = ()
        for layer_past_states in past:


            reordered_layer_past_states = ()
            for layer_past_state in layer_past_states:

                reordered_layer_past_states = reordered_layer_past_states + (
                    layer_past_state.index_select(0, beam_idx),
                )

            assert reordered_layer_past_states[0].shape == layer_past_states[0].shape
            assert len(reordered_layer_past_states) == len(layer_past_states)

            reordered_decoder_past = reordered_decoder_past + (reordered_layer_past_states,)
        return reordered_decoder_past


    def _prepare_encoder_decoder_kwargs_for_generation(
        self, input_ids: torch.LongTensor, model_kwargs
    ) -> Dict[str, Any]:
        if "encoder_outputs" not in model_kwargs:

            encoder = self.get_encoder()
            encoder_kwargs = {
                argument: value
                for argument, value in model_kwargs.items()
                if not (argument.startswith("decoder_") or argument.startswith("cross_attn") or argument.startswith("event_"))
            }
            model_kwargs["encoder_outputs"]: ModelOutput = encoder(input_ids, return_dict=True, **encoder_kwargs)
        return model_kwargs