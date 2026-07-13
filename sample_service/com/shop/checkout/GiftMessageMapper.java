package com.shop.checkout;

public class GiftMessageMapper {
    // Added in v2026.07.11-3 (commit 9f3ab21): gift-message support
    public String map(String giftMessage) {
        // BUG: giftMessage is null when the customer leaves the field empty
        return giftMessage.trim().isEmpty() ? null : giftMessage.trim();  // line 42 in real file
    }
}
