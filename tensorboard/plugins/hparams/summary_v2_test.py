# Copyright 2019 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import abc
import collections
import os
import time
import unittest

from google.protobuf import text_format
import six

try:
  # python version >= 3.3
  from unittest import mock  # pylint: disable=g-import-not-at-top
except ImportError:
  import mock  # pylint: disable=g-import-not-at-top,unused-import

from tensorboard import test
from tensorboard.compat import tf
from tensorboard.compat.proto import summary_pb2
from tensorboard.plugins.hparams import api_pb2
from tensorboard.plugins.hparams import metadata
from tensorboard.plugins.hparams import plugin_data_pb2
from tensorboard.plugins.hparams import summary_v2 as hp


if tf.__version__ == "stub":
  tf = None


if tf is not None:
  tf.compat.v1.enable_eager_execution()


requires_tf = unittest.skipIf(tf is None, "Requires TensorFlow.")


class HParamsTest(test.TestCase):
  """Tests for `summary_v2.hparams` and `summary_v2.hparams_pb`."""

  def setUp(self):
    self.logdir = os.path.join(self.get_temp_dir(), "logs")
    self.hparams = {
        hp.HParam("learning_rate", hp.RealInterval(1e-2, 1e-1)): 0.02,
        hp.HParam("dense_layers", hp.IntInterval(2, 7)): 5,
        hp.HParam("optimizer", hp.Discrete(["adam", "sgd"])): "adam",
        hp.HParam("who_knows_what"): "???",
        hp.HParam(
            "magic",
            hp.Discrete([False, True]),
            display_name="~*~ Magic ~*~",
            description="descriptive",
        ): True,
        "dropout": 0.3,
    }
    self.normalized_hparams = {
        "learning_rate": 0.02,
        "dense_layers": 5,
        "optimizer": "adam",
        "who_knows_what": "???",
        "magic": True,
        "dropout": 0.3,
    }
    self.start_time_secs = 123.45
    self.group_name = "big_sha"

    self.expected_session_start_pb = plugin_data_pb2.SessionStartInfo()
    text_format.Merge(
        """
        hparams { key: "learning_rate" value { number_value: 0.02 } }
        hparams { key: "dense_layers" value { number_value: 5 } }
        hparams { key: "optimizer" value { string_value: "adam" } }
        hparams { key: "who_knows_what" value { string_value: "???" } }
        hparams { key: "magic" value { bool_value: true } }
        hparams { key: "dropout" value { number_value: 0.3 } }
        group_name: "big_sha"  # we'll ignore this field when asserting equality
        """,
        self.expected_session_start_pb,
    )
    self.expected_session_start_pb.start_time_secs = self.start_time_secs

  def _check_summary(self, summary_pb):
    """Test that a summary contains exactly the expected hparams PB."""
    values = summary_pb.value
    self.assertEqual(len(values), 1, values)
    actual_value = values[0]
    self.assertEqual(
        actual_value.metadata.plugin_data.plugin_name,
        metadata.PLUGIN_NAME,
    )
    plugin_content = actual_value.metadata.plugin_data.content
    info_pb = metadata.parse_session_start_info_plugin_data(plugin_content)
    # Ignore the `group_name` field; its properties are checked separately.
    info_pb.group_name = self.expected_session_start_pb.group_name
    self.assertEqual(info_pb, self.expected_session_start_pb)

  def _check_logdir(self, logdir):
    """Test that the hparams summary was written to `logdir`."""
    self._check_summary(_get_unique_summary(self, logdir))

  @requires_tf
  def test_eager(self):
    with tf.compat.v2.summary.create_file_writer(self.logdir).as_default():
      result = hp.hparams(self.hparams, start_time_secs=self.start_time_secs)
      self.assertTrue(result)
    self._check_logdir(self.logdir)

  @requires_tf
  def test_graph_mode(self):
    with \
        tf.compat.v1.Graph().as_default(), \
        tf.compat.v1.Session() as sess, \
        tf.compat.v2.summary.create_file_writer(self.logdir).as_default() as w:
      sess.run(w.init())
      summ = hp.hparams(self.hparams, start_time_secs=self.start_time_secs)
      self.assertTrue(sess.run(summ))
      sess.run(w.flush())
    self._check_logdir(self.logdir)

  @requires_tf
  def test_eager_no_default_writer(self):
    result = hp.hparams(self.hparams, start_time_secs=self.start_time_secs)
    self.assertFalse(result)  # no default writer

  def test_pb_contents(self):
    result = hp.hparams_pb(self.hparams, start_time_secs=self.start_time_secs)
    self._check_summary(result)

  def test_pb_is_tensorboard_copy_of_proto(self):
    result = hp.hparams_pb(self.hparams, start_time_secs=self.start_time_secs)
    self.assertIsInstance(result, summary_pb2.Summary)
    if tf is not None:
      self.assertNotIsInstance(result, tf.compat.v1.Summary)

  def test_consistency_across_string_key_and_object_key(self):
    hparams_1 = {
        hp.HParam("optimizer", hp.Discrete(["adam", "sgd"])): "adam",
        "learning_rate": 0.02,
    }
    hparams_2 = {
        "optimizer": "adam",
        hp.HParam("learning_rate", hp.RealInterval(1e-2, 1e-1)): 0.02,
    }
    self.assertEqual(
        hp.hparams_pb(hparams_1, start_time_secs=self.start_time_secs),
        hp.hparams_pb(hparams_2, start_time_secs=self.start_time_secs),
    )

  def test_duplicate_hparam_names_across_object_and_string(self):
    hparams = {
        "foo": 1,
        hp.HParam("foo"): 1,
    }
    with six.assertRaisesRegex(
        self, ValueError, "multiple values specified for hparam 'foo'"):
      hp.hparams_pb(hparams)

  def test_duplicate_hparam_names_from_two_objects(self):
    hparams = {
        hp.HParam("foo"): 1,
        hp.HParam("foo"): 1,
    }
    with six.assertRaisesRegex(
        self, ValueError, "multiple values specified for hparam 'foo'"):
      hp.hparams_pb(hparams)

  def test_invariant_under_permutation(self):
    # In particular, the group name should be the same.
    hparams_1 = {
        "optimizer": "adam",
        "learning_rate": 0.02,
    }
    hparams_2 = {
        "learning_rate": 0.02,
        "optimizer": "adam",
    }
    self.assertEqual(
        hp.hparams_pb(hparams_1, start_time_secs=self.start_time_secs),
        hp.hparams_pb(hparams_2, start_time_secs=self.start_time_secs),
    )

  def test_group_name_differs_across_hparams_values(self):
    hparams_1 = {"foo": 1, "bar": 2, "baz": 4}
    hparams_2 = {"foo": 1, "bar": 3, "baz": 4}
    def get_group_name(hparams):
      summary_pb = hp.hparams_pb(hparams)
      values = summary_pb.value
      self.assertEqual(len(values), 1, values)
      actual_value = values[0]
      self.assertEqual(
          actual_value.metadata.plugin_data.plugin_name,
          metadata.PLUGIN_NAME,
      )
      plugin_content = actual_value.metadata.plugin_data.content
      info = metadata.parse_session_start_info_plugin_data(plugin_content)
      return info.group_name

    self.assertNotEqual(get_group_name(hparams_1), get_group_name(hparams_2))


class HParamsConfigTest(test.TestCase):
  def setUp(self):
    self.logdir = os.path.join(self.get_temp_dir(), "logs")

    self.hparams = [
        hp.HParam("learning_rate", hp.RealInterval(1e-2, 1e-1)),
        hp.HParam("dense_layers", hp.IntInterval(2, 7)),
        hp.HParam("optimizer", hp.Discrete(["adam", "sgd"])),
        hp.HParam("who_knows_what"),
        hp.HParam(
            "magic",
            hp.Discrete([False, True]),
            display_name="~*~ Magic ~*~",
            description="descriptive",
        ),
    ]
    self.metrics = [
        hp.Metric("samples_per_second"),
        hp.Metric(group="train", tag="batch_loss", display_name="loss (train)"),
        hp.Metric(
            group="validation",
            tag="epoch_accuracy",
            display_name="accuracy (val.)",
            description="Accuracy on the _validation_ dataset.",
            dataset_type=hp.Metric.VALIDATION,
        ),
    ]
    self.time_created_secs = 1555624767.0

    self.expected_experiment_pb = api_pb2.Experiment()
    text_format.Merge(
        """
        time_created_secs: 1555624767.0
        hparam_infos {
          name: "learning_rate"
          type: DATA_TYPE_FLOAT64
          domain_interval {
            min_value: 0.01
            max_value: 0.1
          }
        }
        hparam_infos {
          name: "dense_layers"
          type: DATA_TYPE_FLOAT64
          domain_interval {
            min_value: 2
            max_value: 7
          }
        }
        hparam_infos {
          name: "optimizer"
          type: DATA_TYPE_STRING
          domain_discrete {
            values {
              string_value: "adam"
            }
            values {
              string_value: "sgd"
            }
          }
        }
        hparam_infos {
          name: "who_knows_what"
        }
        hparam_infos {
          name: "magic"
          type: DATA_TYPE_BOOL
          display_name: "~*~ Magic ~*~"
          description: "descriptive"
          domain_discrete {
            values {
              bool_value: false
            }
            values {
              bool_value: true
            }
          }
        }
        metric_infos {
          name {
            tag: "samples_per_second"
          }
        }
        metric_infos {
          name {
            group: "train"
            tag: "batch_loss"
          }
          display_name: "loss (train)"
        }
        metric_infos {
          name {
            group: "validation"
            tag: "epoch_accuracy"
          }
          display_name: "accuracy (val.)"
          description: "Accuracy on the _validation_ dataset."
          dataset_type: DATASET_VALIDATION
        }
        """,
        self.expected_experiment_pb,
    )

  def _check_summary(self, summary_pb):
    """Test that a summary contains exactly the expected experiment PB."""
    values = summary_pb.value
    self.assertEqual(len(values), 1, values)
    actual_value = values[0]
    self.assertEqual(
        actual_value.metadata.plugin_data.plugin_name,
        metadata.PLUGIN_NAME,
    )
    plugin_content = actual_value.metadata.plugin_data.content
    self.assertEqual(
        metadata.parse_experiment_plugin_data(plugin_content),
        self.expected_experiment_pb,
    )

  def _check_logdir(self, logdir):
    """Test that the experiment summary was written to `logdir`."""
    self._check_summary(_get_unique_summary(self, logdir))

  @requires_tf
  def test_eager(self):
    with tf.compat.v2.summary.create_file_writer(self.logdir).as_default():
      result = hp.hparams_config(
          hparams=self.hparams,
          metrics=self.metrics,
          time_created_secs=self.time_created_secs,
      )
      self.assertTrue(result)
    self._check_logdir(self.logdir)

  @requires_tf
  def test_graph_mode(self):
    with \
        tf.compat.v1.Graph().as_default(), \
        tf.compat.v1.Session() as sess, \
        tf.compat.v2.summary.create_file_writer(self.logdir).as_default() as w:
      sess.run(w.init())
      summ = hp.hparams_config(
          hparams=self.hparams,
          metrics=self.metrics,
          time_created_secs=self.time_created_secs,
      )
      self.assertTrue(sess.run(summ))
      sess.run(w.flush())
    self._check_logdir(self.logdir)

  @requires_tf
  def test_eager_no_default_writer(self):
    result = hp.hparams_config(
        hparams=self.hparams,
        metrics=self.metrics,
        time_created_secs=self.time_created_secs,
    )
    self.assertFalse(result)  # no default writer

  def test_pb_contents(self):
    result = hp.hparams_config_pb(
        hparams=self.hparams,
        metrics=self.metrics,
        time_created_secs=self.time_created_secs,
    )
    self._check_summary(result)

  def test_pb_is_tensorboard_copy_of_proto(self):
    result = hp.hparams_config_pb(
        hparams=self.hparams,
        metrics=self.metrics,
        time_created_secs=self.time_created_secs,
    )
    self.assertIsInstance(result, summary_pb2.Summary)
    if tf is not None:
      self.assertNotIsInstance(result, tf.compat.v1.Summary)


def _get_unique_summary(self, logdir):
  """Get the unique `Summary` stored in `logdir`.

  Specifically, `logdir` must be a directory containing exactly one
  entry, which must be an events file of whose events exactly one is a
  summary. This unique summary will be returned.

  Args:
    self: A `TestCase` object, used for assertions.
    logdir: String path to a logdir.

  Returns:
    A `summary_pb2.Summary` object.
  """
  files = os.listdir(logdir)
  self.assertEqual(len(files), 1, files)
  events_file = os.path.join(logdir, files[0])
  summaries = [
      event.summary
      for event in tf.compat.v1.train.summary_iterator(events_file)
      if event.WhichOneof("what") == "summary"
  ]
  self.assertEqual(len(summaries), 1, summaries)
  return summaries[0]


class IntIntervalTest(test.TestCase):
  def test_simple(self):
    domain = hp.IntInterval(3, 7)
    self.assertEqual(domain.min_value, 3)
    self.assertEqual(domain.max_value, 7)
    self.assertEqual(domain.dtype, int)

  def test_singleton_domain(self):
    domain = hp.IntInterval(61, 61)
    self.assertEqual(domain.min_value, 61)
    self.assertEqual(domain.max_value, 61)
    self.assertEqual(domain.dtype, int)

  def test_non_ints(self):
    with six.assertRaisesRegex(
        self, TypeError, "min_value must be an int: -inf"):
      hp.IntInterval(float("-inf"), 0)
    with six.assertRaisesRegex(
        self, TypeError, "max_value must be an int: 'eleven'"):
      hp.IntInterval(7, "eleven")

  def test_backward_endpoints(self):
    with six.assertRaisesRegex(
        self, ValueError, "123 > 45"):
      hp.IntInterval(123, 45)


class RealIntervalTest(test.TestCase):
  def test_simple(self):
    domain = hp.RealInterval(3.1, 7.7)
    self.assertEqual(domain.min_value, 3.1)
    self.assertEqual(domain.max_value, 7.7)
    self.assertEqual(domain.dtype, float)

  def test_singleton_domain(self):
    domain = hp.RealInterval(61.318, 61.318)
    self.assertEqual(domain.min_value, 61.318)
    self.assertEqual(domain.max_value, 61.318)
    self.assertEqual(domain.dtype, float)

  def test_infinite_domain(self):
    inf = float("inf")
    domain = hp.RealInterval(-inf, inf)
    self.assertEqual(domain.min_value, -inf)
    self.assertEqual(domain.max_value, inf)
    self.assertEqual(domain.dtype, float)

  def test_non_ints(self):
    with six.assertRaisesRegex(
        self, TypeError, "min_value must be a float: True"):
      hp.RealInterval(True, 2.0)
    with six.assertRaisesRegex(
        self, TypeError, "max_value must be a float: 'wat'"):
      hp.RealInterval(1.2, "wat")

  def test_backward_endpoints(self):
    with six.assertRaisesRegex(
        self, ValueError, "2.1 > 1.2"):
      hp.RealInterval(2.1, 1.2)


class DiscreteTest(test.TestCase):
  def test_simple(self):
    domain = hp.Discrete([1, 2, 5])
    self.assertEqual(domain.values, [1, 2, 5])
    self.assertEqual(domain.dtype, int)

  def test_values_sorted(self):
    domain = hp.Discrete([2, 3, 1])
    self.assertEqual(domain.values, [1, 2, 3])
    self.assertEqual(domain.dtype, int)

  def test_empty_with_explicit_dtype(self):
    domain = hp.Discrete([], dtype=bool)
    self.assertIs(domain.dtype, bool)
    self.assertEqual(domain.values, [])

  def test_empty_with_unspecified_dtype(self):
    with six.assertRaisesRegex(
        self, ValueError, "Empty domain with no dtype specified"):
      hp.Discrete([])

  def test_dtype_mismatch(self):
    with six.assertRaisesRegex(
        self, TypeError, r"dtype mismatch: not isinstance\(2, str\)"):
      hp.Discrete(["one", 2])


if __name__ == "__main__":
  if tf is not None:
    tf.test.main()
  else:
    test.main()
