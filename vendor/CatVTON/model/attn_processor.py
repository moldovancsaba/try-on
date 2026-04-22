from torch.nn import functional as F
import torch


class SkipAttnProcessor(torch.nn.Module):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__()

    def __call__(
        self,
        attn,
        hidden_states,
        encoder_hidden_states=None,
        attention_mask=None,
        temb=None,
    ):
        # CatVTON Identity Anchor: If face features are provided, perform cross-attention.
        # Otherwise, keep skipping to maintain garment focus.
        if encoder_hidden_states is not None:
             # Logic to handle the projection will be handled in the pipeline or here
             # For a "Brutally Pragmatic" fix, we'll use a standard attention handshake
             # if identity features are present.
             return AttnProcessor2_0()(attn, hidden_states, encoder_hidden_states, attention_mask, temb)
        return hidden_states

class AttnProcessor2_0(torch.nn.Module):
    r"""
    Processor for implementing scaled dot-product attention (enabled by default if you're using PyTorch 2.0).
    """

    def __init__(
        self,
        hidden_size=None,
        cross_attention_dim=None,
        **kwargs
    ):
        super().__init__()
        if not hasattr(F, "scaled_dot_product_attention"):
            raise ImportError("AttnProcessor2_0 requires PyTorch 2.0, to use it, please upgrade PyTorch to 2.0.")

    def __call__(
        self,
        attn,
        hidden_states,
        encoder_hidden_states=None,
        attention_mask=None,
        temb=None,
        *args,
        **kwargs,
    ):
        residual = hidden_states

        if attn.spatial_norm is not None:
            hidden_states = attn.spatial_norm(hidden_states, temb)

        input_ndim = hidden_states.ndim

        if input_ndim == 4:
            batch_size, channel, height, width = hidden_states.shape
            hidden_states = hidden_states.view(batch_size, channel, height * width).transpose(1, 2)

        batch_size, sequence_length, _ = (
            hidden_states.shape if encoder_hidden_states is None else encoder_hidden_states.shape
        )

        if attention_mask is not None:
            attention_mask = attn.prepare_attention_mask(attention_mask, sequence_length, batch_size)
            # scaled_dot_product_attention expects attention_mask shape to be
            # (batch, heads, source_length, target_length)
            attention_mask = attention_mask.view(batch_size, attn.heads, -1, attention_mask.shape[-1])

        if attn.group_norm is not None:
            hidden_states = attn.group_norm(hidden_states.transpose(1, 2)).transpose(1, 2)

        query = attn.to_q(hidden_states)

        if encoder_hidden_states is None:
            encoder_hidden_states = hidden_states
        elif attn.norm_cross:
            encoder_hidden_states = attn.norm_encoder_hidden_states(encoder_hidden_states)

        key = attn.to_k(encoder_hidden_states)
        value = attn.to_v(encoder_hidden_states)

        inner_dim = key.shape[-1]
        head_dim = inner_dim // attn.heads

        query = query.view(batch_size, -1, attn.heads, head_dim).transpose(1, 2)

        key = key.view(batch_size, -1, attn.heads, head_dim).transpose(1, 2)
        value = value.view(batch_size, -1, attn.heads, head_dim).transpose(1, 2)

        # Use chunked attention calculation on MPS to avoid massive intermediate buffer allocations
        # and "Invalid buffer size: 9.00 GiB" errors. 
        # By processing in slices (chunk_size), we keep individual allocations within the Mac's limits.
        if query.device.type == "mps":
            q = query * (head_dim ** -0.5)
            q = q.to(key.dtype)
            
            chunk_size = 1024 # Process in smaller slices to stay well under 8GB/9GB buffers
            hidden_states = torch.zeros_like(query)
            
            for i in range(0, query.shape[-2], chunk_size):
                end = min(i + chunk_size, query.shape[-2])
                q_slice = q[:, :, i:end, :]
                
                # Local attention weight calculation for this slice
                attn_slice = torch.matmul(q_slice, key.transpose(-1, -2))
                if attention_mask is not None:
                    # Handle mask slicing if it has a sequence dimension
                    m_slice = attention_mask[:, :, i:end, :] if attention_mask.ndim == 4 else attention_mask
                    attn_slice += m_slice
                
                attn_slice = torch.softmax(attn_slice, dim=-1)
                hidden_states[:, :, i:end, :] = torch.matmul(attn_slice, value)
        else:
            hidden_states = F.scaled_dot_product_attention(
                query, key, value, attn_mask=attention_mask, dropout_p=0.0, is_causal=False
            )

        hidden_states = hidden_states.transpose(1, 2).reshape(batch_size, -1, attn.heads * head_dim)
        hidden_states = hidden_states.to(query.dtype)

        # linear proj
        hidden_states = attn.to_out[0](hidden_states)
        # dropout
        hidden_states = attn.to_out[1](hidden_states)

        if input_ndim == 4:
            hidden_states = hidden_states.transpose(-1, -2).reshape(batch_size, channel, height, width)

        if attn.residual_connection:
            hidden_states = hidden_states + residual

        hidden_states = hidden_states / attn.rescale_output_factor

        return hidden_states
   