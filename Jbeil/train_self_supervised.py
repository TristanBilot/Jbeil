import math
import logging
import time
import sys
import argparse
import torch
import numpy as np
import pickle
import random
from tqdm import tqdm
from pathlib import Path

from evaluation.evaluation import eval_edge_prediction, eval_edge_detection
from model.tgn import TGN
from utils.utils import EarlyStopMonitor, RandEdgeSampler, get_neighbor_finder
from utils.data_processing import get_data, compute_time_statistics

torch.manual_seed(0)
np.random.seed(0)
random.seed(0)

### Argument and global variables
parser = argparse.ArgumentParser('Jbeil self-supervised training')
parser.add_argument('--exp', type=str, help='Inductive experiment as in TAO (Exp1, Exp2, Exp3)')
parser.add_argument('--induct', type=float, default=0.3)
parser.add_argument('--n', type=int, default=1000000)
parser.add_argument('-d', '--data', type=str, help='Dataset name (eg. auth or pivoting)',
                    default='auth')
parser.add_argument('--bs', type=int, default=200, help='Batch_size')
parser.add_argument('--prefix', type=str, default='', help='Prefix to name the checkpoints')
parser.add_argument('--n_degree', type=int, default=10, help='Number of neighbors to sample')
parser.add_argument('--n_head', type=int, default=2, help='Number of heads used in attention layer')
parser.add_argument('--n_epoch', type=int, default=10, help='Number of epochs')
parser.add_argument('--n_layer', type=int, default=1, help='Number of network layers')
parser.add_argument('--lr', type=float, default=0.0001, help='Learning rate')
parser.add_argument('--patience', type=int, default=5, help='Patience for early stopping')
parser.add_argument('--n_runs', type=int, default=1, help='Number of runs')
parser.add_argument('--drop_out', type=float, default=0.1, help='Dropout probability')
parser.add_argument('--gpu', type=int, default=0, help='Idx for the gpu to use')
parser.add_argument('--node_dim', type=int, default=100, help='Dimensions of the node embedding')
parser.add_argument('--time_dim', type=int, default=100, help='Dimensions of the time embedding')
parser.add_argument('--backprop_every', type=int, default=1, help='Every how many batches to '
                                                                  'backprop')
parser.add_argument('--use_memory', action='store_true',
                    help='Whether to augment the model with a node memory')
parser.add_argument('--embedding_module', type=str, default="graph_attention", choices=[
  "graph_attention", "graph_sum", "identity", "time"], help='Type of embedding module')
parser.add_argument('--message_function', type=str, default="identity", choices=[
  "mlp", "identity"], help='Type of message function')
parser.add_argument('--memory_updater', type=str, default="gru", choices=[
  "gru", "rnn"], help='Type of memory updater')
parser.add_argument('--aggregator', type=str, default="last", help='Type of message '
                                                                        'aggregator')
parser.add_argument('--memory_update_at_end', action='store_true',
                    help='Whether to update memory at the end or at the start of the batch')
parser.add_argument('--message_dim', type=int, default=100, help='Dimensions of the messages')
# parser.add_argument('--memory_dim', type=int, default=172, help='Dimensions of the memory for '
#                                                                 'each user')
parser.add_argument('--memory_dim', type=int, default=10, help='Dimensions of the memory for '
                                                                'each user')
parser.add_argument('--different_new_nodes', action='store_true',
                    help='Whether to use disjoint set of new nodes for train and val')
parser.add_argument('--uniform', action='store_true',
                    help='take uniform sampling from temporal neighbors')
parser.add_argument('--randomize_features', action='store_true',
                    help='Whether to randomize node features')
parser.add_argument('--use_destination_embedding_in_message', action='store_true',
                    help='Whether to use the embedding of the destination node as part of the message')
parser.add_argument('--use_source_embedding_in_message', action='store_true',
                    help='Whether to use the embedding of the source node as part of the message')
parser.add_argument('--dyrep', action='store_true',
                    help='Whether to run the dyrep model')


try:
  args = parser.parse_args()
except:
  parser.print_help()
  sys.exit(0)

BATCH_SIZE = args.bs
NUM_NEIGHBORS = args.n_degree
NUM_NEG = 1
NUM_EPOCH = args.n_epoch
NUM_HEADS = args.n_head
DROP_OUT = args.drop_out
GPU = args.gpu
DATA = args.data
NUM_LAYER = args.n_layer
LEARNING_RATE = args.lr
NODE_DIM = args.node_dim
TIME_DIM = args.time_dim
USE_MEMORY = args.use_memory
MESSAGE_DIM = args.message_dim
MEMORY_DIM = args.memory_dim
induct = args.induct
n = args.n

Path("./saved_models/").mkdir(parents=True, exist_ok=True)
Path("./saved_checkpoints/").mkdir(parents=True, exist_ok=True)
MODEL_SAVE_PATH = f'./saved_models/{args.prefix}-{args.node_dim}-{args.data}.pth'
get_checkpoint_path = lambda \
    epoch: f'./saved_checkpoints/{args.prefix}-{args.data}-{args.node_dim}-{epoch}.pth'

### set up logger
Path("log/").mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename='log/{}.log'.format(str(time.time())),
                    filemode='a',                    
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.DEBUG)
logging.info("Running Urban Planning")
logger = logging.getLogger('urbanGUI')
logger.info(args)

### Extract data for training, validation and testing
node_features, edge_features, full_data, train_data, val_data, test_data, new_node_val_data, \
new_node_test_data = get_data(DATA, induct, n,
                              different_new_nodes_between_val_and_test=args.different_new_nodes, randomize_features=args.randomize_features, logger = logger)

# Inductive experiments from TAO happen here
def compute_inductive_nodes(inductive_experiment, nb_nodes):
  if inductive_experiment == "None":
      return None

  # Removes 30% of nodes with their connections, without any malicious nodes inside.
  if inductive_experiment == "Exp1":
      percent = int(nb_nodes * 0.3)

      available_nodes = set(range(1, nb_nodes)) - set(
          malicious_src_nodes
      )
      sample = torch.tensor(random.sample(available_nodes, percent))

      return sample

  # Removes 30% of nodes with their connections, including all malicious nodes (4)
  if inductive_experiment == "Exp2":
      percent = int(nb_nodes * 0.3)

      available_nodes = set(range(1, nb_nodes))
      sample = torch.tensor(random.sample(available_nodes, percent))
      sample[: len(malicious_src_nodes)] = torch.tensor(
          malicious_src_nodes
      )

      return sample

  # Removes 50% of nodes with their connections, including all malicious nodes (4)
  if inductive_experiment == "Exp3":
      percent = int(nb_nodes * 0.5)

      available_nodes = set(range(1, nb_nodes))
      sample = torch.tensor(random.sample(available_nodes, percent))
      sample[: len(malicious_src_nodes)] = torch.tensor(
          malicious_src_nodes
      )

      return sample

  raise ValueError("Invalid experiment name.")

def apply_inductive_experiment(data, inductive_nodes):
    sample = inductive_nodes

    mask = (torch.tensor(data.sources).unsqueeze(1) == sample).any(1) | (
        torch.tensor(data.destinations).unsqueeze(1) == sample
    ).any(1)
    keep_edges = ~mask

    data.sources = data.sources[keep_edges]
    data.destinations = data.destinations[keep_edges]
    data.labels = data.labels[keep_edges]
    data.timestamps = data.timestamps[keep_edges]
    data.edge_idxs = data.edge_idxs[keep_edges]

    return data

OPTC_MALICIOUS_SRC_NODES = [201, 402, 501]
LANL_MALICIOUS_SRC_NODES = [9660, 9910, 10957, 12637]

malicious_src_nodes = OPTC_MALICIOUS_SRC_NODES if args.data == "OPTC" else LANL_MALICIOUS_SRC_NODES
nb_nodes = 975 if args.data == "OPTC" else 15611 

# We update train data to remove inductive nodes from training
inductive_nodes = compute_inductive_nodes(args.exp, nb_nodes)
train_data = apply_inductive_experiment(train_data, inductive_nodes)


# Initialize training neighbor finder to retrieve temporal graph
train_ngh_finder = get_neighbor_finder(train_data, args.uniform)

# Initialize validation and test neighbor finder to retrieve temporal graph
full_ngh_finder = get_neighbor_finder(full_data, args.uniform)

# Initialize negative samplers. Set seeds for validation and testing so negatives are the same
# across different runs
# NB: in the inductive setting, negatives are sampled only amongst other new nodes
train_rand_sampler = RandEdgeSampler(train_data.sources, train_data.destinations)
val_rand_sampler = RandEdgeSampler(full_data.sources, full_data.destinations, seed=0)
nn_val_rand_sampler = RandEdgeSampler(new_node_val_data.sources, new_node_val_data.destinations,
                                      seed=1)
test_rand_sampler = RandEdgeSampler(full_data.sources, full_data.destinations, seed=2)
nn_test_rand_sampler = RandEdgeSampler(new_node_test_data.sources,
                                       new_node_test_data.destinations,
                                       seed=3)

# Set device
device_string = 'cuda:{}'.format(GPU) if torch.cuda.is_available() else 'cpu'
logger.info("$$$ ==> {}".format(device_string))
device = torch.device(device_string)
logger.info("### ==> {}".format(device))

# Compute time statistics
mean_time_shift_src, std_time_shift_src, mean_time_shift_dst, std_time_shift_dst = \
  compute_time_statistics(full_data.sources, full_data.destinations, full_data.timestamps)

for i in range(args.n_runs):
  results_path = "results/{}_{}.pkl".format(args.prefix, i) if i > 0 else "results/{}.pkl".format(args.prefix)
  Path("results/").mkdir(parents=True, exist_ok=True)

  # Initialize Model
  tgn = TGN(neighbor_finder=train_ngh_finder, node_features=node_features,
            node_embedding_size=NODE_DIM,
            edge_features=edge_features, device=device,
            n_layers=NUM_LAYER,
            n_heads=NUM_HEADS, dropout=DROP_OUT, use_memory=USE_MEMORY,
            message_dimension=MESSAGE_DIM, memory_dimension=MEMORY_DIM,
            memory_update_at_start=not args.memory_update_at_end,
            embedding_module_type=args.embedding_module,
            message_function=args.message_function,
            aggregator_type=args.aggregator,
            memory_updater_type=args.memory_updater,
            n_neighbors=NUM_NEIGHBORS,
            mean_time_shift_src=mean_time_shift_src, std_time_shift_src=std_time_shift_src,
            mean_time_shift_dst=mean_time_shift_dst, std_time_shift_dst=std_time_shift_dst,
            use_destination_embedding_in_message=args.use_destination_embedding_in_message,
            use_source_embedding_in_message=args.use_source_embedding_in_message,
            dyrep=args.dyrep)
  criterion = torch.nn.BCELoss()
  optimizer = torch.optim.Adam(tgn.parameters(), lr=LEARNING_RATE)
  tgn = tgn.to(device)

  num_instance = len(train_data.sources)
  num_batch = math.ceil(num_instance / BATCH_SIZE)

  logger.info('num of training instances: {}'.format(num_instance))
  logger.info('num of batches per epoch: {}'.format(num_batch))
  idx_list = np.arange(num_instance)

  new_nodes_val_aps = []
  val_aps = []
  epoch_times = []
  total_epoch_times = []
  train_losses = []

  early_stopper = EarlyStopMonitor(max_round=args.patience)
  
  for epoch in range(NUM_EPOCH):
    start_epoch = time.time()
    ### Training

    # Reinitialize memory of the model at the start of each epoch
    if USE_MEMORY:
      tgn.memory.__init_memory__()

    # Train using only training graph
    tgn.set_neighbor_finder(train_ngh_finder)
    m_loss = []

    # prog = tqdm(desc='Batches parsed', total=num_batch)
    logger.info('start {} epoch'.format(epoch))
    for k in range(0, num_batch, args.backprop_every):
      # prog.update(num_batch-k)
      if (k%10000 == 0):
          logger.info('Batch number: {}/{}'.format(k, num_batch))
      loss = 0
      optimizer.zero_grad()

      # Custom loop to allow to perform backpropagation only every a certain number of batches
      for j in range(args.backprop_every):
        batch_idx = k + j

        if batch_idx >= num_batch:
          continue

        start_idx = batch_idx * BATCH_SIZE
        end_idx = min(num_instance, start_idx + BATCH_SIZE)
        sources_batch, destinations_batch = train_data.sources[start_idx:end_idx], \
                                            train_data.destinations[start_idx:end_idx]
        edge_idxs_batch = train_data.edge_idxs[start_idx: end_idx]
        timestamps_batch = train_data.timestamps[start_idx:end_idx]

        size = len(sources_batch)
        _, negatives_batch = train_rand_sampler.sample(size)

        with torch.no_grad():
          pos_label = torch.ones(size, dtype=torch.float, device=device)
          neg_label = torch.zeros(size, dtype=torch.float, device=device)

        tgn = tgn.train()
        pos_prob, neg_prob = tgn.compute_edge_probabilities(sources_batch, destinations_batch, negatives_batch,
                                                            timestamps_batch, edge_idxs_batch, NUM_NEIGHBORS)

        loss += criterion(pos_prob.squeeze(), pos_label) + criterion(neg_prob.squeeze(), neg_label)

      loss /= args.backprop_every

      loss.backward()
      optimizer.step()
      m_loss.append(loss.item())

      # Detach memory after 'args.backprop_every' number of batches so we don't backpropagate to
      # the start of time
      if USE_MEMORY:
        tgn.memory.detach_memory()

    epoch_time = time.time() - start_epoch
    print(epoch_time)
    epoch_times.append(epoch_time)
    print(f"Mean loss: {np.mean(m_loss):.4f}")

    ### Validation
    # Validation uses the full graph
    tgn.set_neighbor_finder(full_ngh_finder)

    if USE_MEMORY:
      # Backup memory at the end of training, so later we can restore it and use it for the
      # validation on unseen nodes
      train_memory_backup = tgn.memory.backup_memory()


    val_ap, val_auc, val_recall, val_precision, val_fp, val_fn, val_tp, val_tn, thresholdOpt = eval_edge_prediction(model=tgn,
                                                            negative_edge_sampler=val_rand_sampler,
                                                            data=val_data,
                                                            n_neighbors=NUM_NEIGHBORS)


    #val_ap, val_auc, FP, FN, TP, TN = eval_edge_prediction(model=tgn,
    #                                                        negative_edge_sampler=val_rand_sampler,
    #                                                        data=val_data,
    #                                                        n_neighbors=NUM_NEIGHBORS)
    if USE_MEMORY:
      val_memory_backup = tgn.memory.backup_memory()
      # Restore memory we had at the end of training to be used when validating on new nodes.
      # Also backup memory after validation so it can be used for testing (since test edges are
      # strictly later in time than validation edges)
      tgn.memory.restore_memory(train_memory_backup)

    # Validate on unseen nodes
    nn_val_ap, nn_val_auc, nn_val_recall, nn_val_precision, nn_val_fp, nn_val_fn, nn_val_tp, nn_val_tn, thresholdOpt = eval_edge_prediction(model=tgn,
                                                                        negative_edge_sampler=val_rand_sampler,
                                                                        data=new_node_val_data,
                                                                        n_neighbors=NUM_NEIGHBORS)
    
    if USE_MEMORY:
      # Restore memory we had at the end of validation
      tgn.memory.restore_memory(val_memory_backup)

    new_nodes_val_aps.append(nn_val_ap)
    val_aps.append(val_ap)
    train_losses.append(np.mean(m_loss))

    # Save temporary results to disk
    pickle.dump({
      "val_aps": val_aps,
      "new_nodes_val_aps": new_nodes_val_aps,
      "train_losses": train_losses,
      "epoch_times": epoch_times,
      "total_epoch_times": total_epoch_times
    }, open(results_path, "wb"))

    total_epoch_time = time.time() - start_epoch
    total_epoch_times.append(total_epoch_time)

    logger.info('epoch: {} took {:.2f}s'.format(epoch, total_epoch_time))
    logger.info('Epoch mean loss: {}'.format(np.mean(m_loss)))
    logger.info(
      'val auc: {}, new node val auc: {}'.format(val_auc, nn_val_auc))
    logger.info(
      'val ap: {}, new node val ap: {}'.format(val_ap, nn_val_ap))
    logger.info(
      'val recall: {}, new node val recall: {}'.format(val_recall, nn_val_recall))
    logger.info(
      'val precision: {}, new node val precision: {}'.format(val_precision, nn_val_precision))
    
    logger.info(
      'val fp: {}, new node val fp: {}'.format(val_fp, nn_val_fp))
    logger.info(
      'val fn: {}, new node val fn: {}'.format(val_fn, nn_val_fn))
    logger.info(
      'val tp: {}, new node val tp: {}'.format(val_tp, nn_val_tp))
    logger.info(
      'val tn: {}, new node val tn: {}'.format(val_tn, nn_val_tn))

    # Early stopping
    if early_stopper.early_stop_check(val_ap):
      logger.info('No improvement over {} epochs, stop training'.format(early_stopper.max_round))
      logger.info(f'Loading the best model at epoch {early_stopper.best_epoch}')
      best_model_path = get_checkpoint_path(early_stopper.best_epoch)
      tgn.load_state_dict(torch.load(best_model_path))
      logger.info(f'Loaded the best model at epoch {early_stopper.best_epoch} for inference')
      tgn.eval()
      break
    else:
      torch.save(tgn.state_dict(), get_checkpoint_path(epoch))

    # if epoch == 1:
    #   break

  # Training has finished, we have loaded the best model, and we want to backup its current
  # memory (which has seen validation edges) so that it can also be used when testing on unseen
  # nodes
  if USE_MEMORY:
    val_memory_backup = tgn.memory.backup_memory()


  ### Test
  logger.info('Started Testing')
  tgn.embedding_module.neighbor_finder = full_ngh_finder
  # test_ap, test_auc, test_recall, test_precision, test_fp, test_fn, test_tp, test_tn = eval_edge_prediction(model=tgn,
  #                                                             negative_edge_sampler=test_rand_sampler,
  #                                                             data=test_data,
  #                                                             n_neighbors=NUM_NEIGHBORS)

  if USE_MEMORY:
    tgn.memory.restore_memory(val_memory_backup)

  # Test on unseen nodes
  eval_edge_detection(model=tgn,
                      negative_edge_sampler=nn_test_rand_sampler,
                      data=new_node_test_data,
                      thresholdOpt=thresholdOpt,
                      n_neighbors=NUM_NEIGHBORS,
                    )

  # logger.info(
  #   'Test statistics: Old nodes -- auc: {}, ap: {}, recall: {}, precision: {}, fp: {}, fn: {}, tp: {}, tn: {}'.format(test_auc, test_ap, test_recall, test_precision, test_fp, test_fn, test_tp, test_tn))
  # logger.info(
  #   'Test statistics: New nodes -- auc: {}, ap: {}, recall: {}, precision: {}, fp: {}, fn: {}, tp: {}, tn: {}'.format(nn_test_auc, nn_test_ap, nn_test_recall, nn_test_precision, nn_test_fp, nn_test_fn, nn_test_tp, nn_test_tn))





  # # Save results for this run
  # pickle.dump({
  #   "val_aps": val_aps,
  #   "new_nodes_val_aps": new_nodes_val_aps,
  #   "test_ap": test_ap,
  #   "new_node_test_ap": nn_test_ap,
  #   "epoch_times": epoch_times,
  #   "train_losses": train_losses,
  #   "total_epoch_times": total_epoch_times
  # }, open(results_path, "wb"))

  logger.info('Saving Jbeil model')
  if USE_MEMORY:
    # Restore memory at the end of validation (save a model which is ready for testing)
    tgn.memory.restore_memory(val_memory_backup)
  torch.save(tgn.state_dict(), MODEL_SAVE_PATH)
  logger.info('Jbeil model saved')
