import json
from pathlib import Path
import pytest

from methods.E2R.paths import observation_file
from scripts.e2r.run_graph_experiment import resolve_device,move_item,supervised_loss


def test_moved_manifest_uses_local_snapshot(tmp_path):
    directory=tmp_path/'different location'/'run';(directory/'observations').mkdir(parents=True)
    snap=directory/'observations/case00001.json';snap.write_text('{"case_id":"case00001"}')
    manifest=directory/'manifest.json';manifest.write_text('{}')
    row={'case_id':'case00001','observation_file':'/old/host/observations/case00001.json'}
    assert observation_file(row,manifest)==snap
    row['observation_file']='observations/case00001.json'
    assert observation_file(row,manifest)==snap


def test_cuda_request_never_silently_uses_cpu(monkeypatch):
    import torch
    monkeypatch.setattr(torch.cuda,'is_available',lambda:False)
    assert resolve_device(torch,'auto').type=='cpu'
    with pytest.raises(RuntimeError,match='No CPU fallback'):resolve_device(torch,'cuda:0')


def test_tensor_and_supervised_label_device_agree():
    import torch
    device=torch.device('cpu')
    item={'x':torch.tensor([[1.],[2.]]),'edge_index':torch.empty((2,0),dtype=torch.long),
          'edge_weight':torch.empty(0),'node_ids':['a','b'],'candidate_indices':[0,1]}
    moved=move_item(item,device)
    def model(x,edge_index,edge_weight):return x[:,0]
    def checked_loss(logits,target,mask):
        assert logits.device==target.device==mask.device==device
        assert int(target)==1 and mask.tolist()==[True,True]
        return torch.nn.functional.cross_entropy(logits[None,:],target[None]),{}
    assert torch.isfinite(supervised_loss(torch,checked_loss,model,moved,'b',device))
