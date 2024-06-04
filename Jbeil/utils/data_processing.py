import numpy as np
import random
import pandas as pd


class Data:
  def __init__(self, sources, destinations, timestamps, edge_idxs, labels):
    self.sources = sources
    self.destinations = destinations
    self.timestamps = timestamps
    self.edge_idxs = edge_idxs
    self.labels = labels
    self.n_interactions = len(sources)
    self.unique_nodes = set(sources) | set(destinations)
    self.n_unique_nodes = len(self.unique_nodes)


def get_data_node_classification(dataset_name, use_validation=False):
  ### Load data and train val test split
  graph_df = pd.read_csv('./data/ml_{}.csv'.format(dataset_name))
  edge_features = np.load('./data/ml_{}.npy'.format(dataset_name))
  node_features = np.load('./data/ml_{}_node.npy'.format(dataset_name))

  val_time, test_time = list(np.quantile(graph_df.ts, [0.70, 0.85]))

  sources = graph_df.u.values
  destinations = graph_df.i.values
  edge_idxs = graph_df.idx.values
  labels = graph_df.label.values
  timestamps = graph_df.ts.values

  random.seed(2020)

  train_mask = timestamps <= val_time if use_validation else timestamps <= test_time
  test_mask = timestamps > test_time
  val_mask = np.logical_and(timestamps <= test_time, timestamps > val_time) if use_validation else test_mask

  full_data = Data(sources, destinations, timestamps, edge_idxs, labels)

  train_data = Data(sources[train_mask], destinations[train_mask], timestamps[train_mask],
                    edge_idxs[train_mask], labels[train_mask])

  val_data = Data(sources[val_mask], destinations[val_mask], timestamps[val_mask],
                  edge_idxs[val_mask], labels[val_mask])

  test_data = Data(sources[test_mask], destinations[test_mask], timestamps[test_mask],
                   edge_idxs[test_mask], labels[test_mask])

  return full_data, node_features, edge_features, train_data, val_data, test_data


def get_data(dataset_name, induct, n, different_new_nodes_between_val_and_test=False, randomize_features=False, logger=None):
  logger.info("inductiveness {}".format(induct))
  ### Load data and train val test split
  # graph_df = pd.read_csv('./data/{}.csv'.format(dataset_name))
  graph_df = pd.read_csv('/mnt/beegfs/home/bilot/these_bilot/Jbeil/Jbeil/data/ml_{}.csv'.format(dataset_name))
  graph_df = graph_df.head(n)
  edge_features = np.load('/mnt/beegfs/home/bilot/these_bilot/Jbeil/Jbeil/data/ml_{}.npy'.format(dataset_name))
  node_features = np.load('/mnt/beegfs/home/bilot/these_bilot/Jbeil/Jbeil/data/ml_{}_node.npy'.format(dataset_name)) 
    
  if randomize_features:
    node_features = np.random.rand(node_features.shape[0], node_features.shape[1])

  val_time, test_time, end_14_days = 140400, 147540, 1209540

  sources = graph_df.u.values
  destinations = graph_df.i.values
  # edge_idxs = graph_df.ts.values
  edge_idxs = graph_df.idx.values
  labels = graph_df.label.values
  timestamps = graph_df.ts.values

  full_data = Data(sources, destinations, timestamps, edge_idxs, labels)

  random.seed(2020)

  node_set = set(sources) | set(destinations)
  # print("----", node_set)
  n_total_unique_nodes = len(node_set)
  # print("----", n_total_unique_nodes)
  # Compute nodes which appear at test time
  test_node_set = set(sources[timestamps > val_time]).union(
    set(destinations[timestamps > val_time]))
  # print("----", test_node_set)
  # Sample nodes which we keep as new nodes (to test inductiveness), so than we have to remove all
  # their edges from training
  ## Modified by Joseph
  # new_test_node_set = set(random.sample(test_node_set, int(0.5 * n_total_unique_nodes)))
  print("***", len(test_node_set))
  print("*** type: ", type(test_node_set))
  print("***", int(induct* n_total_unique_nodes))
  new_test_node_set = set(random.sample(sorted(test_node_set), int(induct* n_total_unique_nodes)))

  # Mask saying for each source and destination whether they are new test nodes
  new_test_source_mask = graph_df.u.map(lambda x: x in new_test_node_set).values
  new_test_destination_mask = graph_df.i.map(lambda x: x in new_test_node_set).values
  print("df done")

  # Mask which is true for edges with both destination and source not being new test nodes (because
  # we want to remove all edges involving any new test node)
  observed_edges_mask = np.logical_and(~new_test_source_mask, ~new_test_destination_mask)

  # For train we keep edges happening before the validation time which do not involve any new node
  # used for inductiveness
  train_mask = np.logical_and(timestamps <= val_time, observed_edges_mask)

  train_data = Data(sources[train_mask], destinations[train_mask], timestamps[train_mask],
                    edge_idxs[train_mask], labels[train_mask])

  # define the new nodes sets for testing inductiveness of the model
  train_node_set = set(train_data.sources).union(train_data.destinations)
  assert len(train_node_set & new_test_node_set) == 0
  new_node_set = node_set - train_node_set

  val_mask = np.logical_and(timestamps <= test_time, timestamps > val_time)
  test_mask = np.logical_and(timestamps > test_time, timestamps < end_14_days)

  if different_new_nodes_between_val_and_test:
    n_new_nodes = len(new_test_node_set) // 2
    val_new_node_set = set(list(new_test_node_set)[:n_new_nodes])
    test_new_node_set = set(list(new_test_node_set)[n_new_nodes:])

    edge_contains_new_val_node_mask = np.array(
      [(a in val_new_node_set or b in val_new_node_set) for a, b in zip(sources, destinations)])
    edge_contains_new_test_node_mask = np.array(
      [(a in test_new_node_set or b in test_new_node_set) for a, b in zip(sources, destinations)])
    new_node_val_mask = np.logical_and(val_mask, edge_contains_new_val_node_mask)
    new_node_test_mask = np.logical_and(test_mask, edge_contains_new_test_node_mask)


  else:
    edge_contains_new_node_mask = np.array(
      [(a in new_node_set or b in new_node_set) for a, b in zip(sources, destinations)])
    new_node_val_mask = np.logical_and(val_mask, edge_contains_new_node_mask)
    new_node_test_mask = np.logical_and(test_mask, edge_contains_new_node_mask)

  # validation and test with all edges
  val_data = Data(sources[val_mask], destinations[val_mask], timestamps[val_mask],
                  edge_idxs[val_mask], labels[val_mask])

  test_data = Data(sources[test_mask], destinations[test_mask], timestamps[test_mask],
                   edge_idxs[test_mask], labels[test_mask])

  # validation and test with edges that at least has one new node (not in training set)
  new_node_val_data = Data(sources[new_node_val_mask], destinations[new_node_val_mask],
                           timestamps[new_node_val_mask],
                           edge_idxs[new_node_val_mask], labels[new_node_val_mask])

  new_node_test_data = Data(sources[new_node_test_mask], destinations[new_node_test_mask],
                            timestamps[new_node_test_mask], edge_idxs[new_node_test_mask],
                            labels[new_node_test_mask])

  logger.info("The dataset has {} interactions, involving {} different nodes".format(full_data.n_interactions,
                                                                      full_data.n_unique_nodes))
  logger.info("The training dataset has {} interactions, involving {} different nodes".format(
    train_data.n_interactions, train_data.n_unique_nodes))
  logger.info("The validation dataset has {} interactions, involving {} different nodes".format(
    val_data.n_interactions, val_data.n_unique_nodes))
  logger.info("The test dataset has {} interactions, involving {} different nodes".format(
    test_data.n_interactions, test_data.n_unique_nodes))
  logger.info("The new node validation dataset has {} interactions, involving {} different nodes".format(
    new_node_val_data.n_interactions, new_node_val_data.n_unique_nodes))
  logger.info("The new node test dataset has {} interactions, involving {} different nodes".format(
    new_node_test_data.n_interactions, new_node_test_data.n_unique_nodes))
  logger.info("{} nodes were used for the inductive testing, i.e. are never seen during training".format(
    len(new_test_node_set)))

  return node_features, edge_features, full_data, train_data, val_data, test_data, \
         new_node_val_data, new_node_test_data


def compute_time_statistics(sources, destinations, timestamps):
  last_timestamp_sources = dict()
  last_timestamp_dst = dict()
  all_timediffs_src = []
  all_timediffs_dst = []
  for k in range(len(sources)):
    source_id = sources[k]
    dest_id = destinations[k]
    c_timestamp = timestamps[k]
    if source_id not in last_timestamp_sources.keys():
      last_timestamp_sources[source_id] = 0
    if dest_id not in last_timestamp_dst.keys():
      last_timestamp_dst[dest_id] = 0
    all_timediffs_src.append(c_timestamp - last_timestamp_sources[source_id])
    all_timediffs_dst.append(c_timestamp - last_timestamp_dst[dest_id])
    last_timestamp_sources[source_id] = c_timestamp
    last_timestamp_dst[dest_id] = c_timestamp
  assert len(all_timediffs_src) == len(sources)
  assert len(all_timediffs_dst) == len(sources)
  mean_time_shift_src = np.mean(all_timediffs_src)
  std_time_shift_src = np.std(all_timediffs_src)
  mean_time_shift_dst = np.mean(all_timediffs_dst)
  std_time_shift_dst = np.std(all_timediffs_dst)

  return mean_time_shift_src, std_time_shift_src, mean_time_shift_dst, std_time_shift_dst
