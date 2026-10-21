"""Native Online Boutique v0.10.1 CartService write/read transaction.

Wire schema: GoogleCloudPlatform/microservices-demo, protos/demo.proto,
tag v0.10.1 (Apache-2.0). Uses actual application RPCs and stored contents.
Fresh channels make Service selector changes observable between transactions.
"""
from __future__ import annotations

import time
import uuid
from contextlib import nullcontext
from functools import lru_cache


@lru_cache(maxsize=1)
def messages():
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
    file = descriptor_pb2.FileDescriptorProto(name="e2r_native_cart.proto", package="hipstershop", syntax="proto3")
    fields = {
        "CartItem": [("product_id", 1, 9, False, None), ("quantity", 2, 5, False, None)],
        "AddItemRequest": [("user_id", 1, 9, False, None), ("item", 2, 11, False, ".hipstershop.CartItem")],
        "GetCartRequest": [("user_id", 1, 9, False, None)],
        "Cart": [("user_id", 1, 9, False, None), ("items", 2, 11, True, ".hipstershop.CartItem")],
        "Empty": [],
    }
    for name, declarations in fields.items():
        message = file.message_type.add(name=name)
        for key, number, kind, repeated, target in declarations:
            field = message.field.add(name=key, number=number, type=kind, label=3 if repeated else 1)
            if target:
                field.type_name = target
    pool = descriptor_pool.DescriptorPool()
    pool.Add(file)
    return {name: message_factory.GetMessageClass(pool.FindMessageTypeByName("hipstershop."+name)) for name in fields}


def cart_transaction(spec, detail=None, channel=None):
    import grpc
    started = time.perf_counter()
    classes = messages()
    detail = {} if detail is None else detail
    timeout = float(spec.get("timeout", 3))
    deadline = time.monotonic()+timeout
    user = "e2r-"+uuid.uuid4().hex
    try:
        # Subchannel pools must not retain a connection across transactions.
        connection = nullcontext(channel) if channel is not None else grpc.insecure_channel(
            spec["url"].removeprefix("grpc://"), options=[("grpc.use_local_subchannel_pool", 1),("grpc.enable_http_proxy", 0)])
        with connection as channel:
            get_cart = channel.unary_unary("/hipstershop.CartService/GetCart",
                request_serializer=classes["GetCartRequest"].SerializeToString, response_deserializer=classes["Cart"].FromString)
            if not spec.get("read_only", False):
                detail['rpc']='AddItem'
                add = channel.unary_unary("/hipstershop.CartService/AddItem",
                    request_serializer=classes["AddItemRequest"].SerializeToString, response_deserializer=classes["Empty"].FromString)
                add(classes["AddItemRequest"](user_id=user, item=classes["CartItem"](product_id="OLJCESPC7Z", quantity=1)),
                    timeout=max(.001, deadline-time.monotonic()))
            detail['rpc']='GetCart'
            cart = get_cart(classes["GetCartRequest"](user_id=user), timeout=max(.001, deadline-time.monotonic()))
            if spec.get("read_only", False):
                ok = not cart.items
            else:
                ok = len(cart.items)==1 and cart.items[0].product_id=="OLJCESPC7Z" and cart.items[0].quantity==1
            if not ok:
                detail['failure']='stored_cart_contents'
            if ok and spec.get('continuity_check'):
                required=spec['continuity_check'];detail['rpc']='GetAcknowledgedCart'
                saved=get_cart(classes['GetCartRequest'](user_id=required['user_id']),
                               timeout=max(.001,deadline-time.monotonic()))
                observed=[{'product_id':item.product_id,'quantity':item.quantity} for item in saved.items]
                ok=observed==[{'product_id':required['product_id'],'quantity':required['quantity']}]
                detail['continuity_preserved']=ok;detail['continuity_observed_items']=observed
                if not ok:detail['failure']='acknowledged_cart_missing_or_changed'
    except (grpc.RpcError, TimeoutError, OSError) as exc:
        ok = False
        detail['failure']=type(exc).__name__
        if isinstance(exc,grpc.RpcError):
            detail['grpc_status']=exc.code().name
    return ok, (time.perf_counter()-started)*1000


def seed_acknowledged_cart(spec,required):
    """Prepare a study-owned user record once, then verify the actual readback."""
    import grpc
    if not required['user_id'].startswith('e2r-ack-') or type(required['quantity']) is not int or required['quantity']<1:
        raise ValueError('state reference must be an explicit study-owned cart')
    classes=messages();started=time.time();deadline=time.monotonic()+float(spec.get('timeout',3))
    with grpc.insecure_channel(spec['url'].removeprefix('grpc://'),options=[('grpc.use_local_subchannel_pool',1),('grpc.enable_http_proxy',0)]) as channel:
        get=channel.unary_unary('/hipstershop.CartService/GetCart',request_serializer=classes['GetCartRequest'].SerializeToString,response_deserializer=classes['Cart'].FromString)
        initial=get(classes['GetCartRequest'](user_id=required['user_id']),timeout=max(.001,deadline-time.monotonic()))
        if initial.items:raise ValueError('reference cart was not reset before this attempt')
        add=channel.unary_unary('/hipstershop.CartService/AddItem',request_serializer=classes['AddItemRequest'].SerializeToString,response_deserializer=classes['Empty'].FromString)
        add(classes['AddItemRequest'](user_id=required['user_id'],item=classes['CartItem'](product_id=required['product_id'],quantity=required['quantity'])),timeout=max(.001,deadline-time.monotonic()))
        observed=get(classes['GetCartRequest'](user_id=required['user_id']),timeout=max(.001,deadline-time.monotonic()))
        items=[{'product_id':x.product_id,'quantity':x.quantity} for x in observed.items]
    if items!=[{'product_id':required['product_id'],'quantity':required['quantity']}]:raise ValueError('reference write/readback not acknowledged')
    return {'status':'acknowledged','started_at':started,'completed_at':time.time(),'required':required,'observed_items':items,
            'evidence':'Actual native AddItem response and GetCart readback before fault injection; no predicted state.'}
