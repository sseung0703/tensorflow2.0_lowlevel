import tensorflow as tf
from tensorflow.python.ops import control_flow_ops

import time
import scipy.io as sio
import numpy as np
from random import shuffle

from nets import ResNet

### define path and hyper-parameter
train_dir   = '/home/dmsl/Documents/tf2.0/test'

model_name   = 'vgg16'
dataset = sio.loadmat('/home/dmsl/Documents/data/tf/cifar10_natural.mat')
dataset_len, *image_size = dataset['train_image'].shape
num_labels = np.max(dataset['train_label'])+1

Optimizer = 'sgd' # 'adam' or 'sgd'
Learning_rate =1e-1

batch_size = 128
val_batch_size = 16
num_epoch = 200
weight_decay = 1e-4

should_log          = 80
save_summaries_secs = 60


def MODEL(model_name, weight_decay, image, label, trainable, is_training):
    end_points = ResNet.model(image, trainable = trainable, is_training = is_training)

    loss = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(label,end_points['Logits']))
    accuracy = tf.reduce_mean(tf.cast(tf.equal(tf.argmax(label, 1), tf.argmax(end_points['Logits'], 1)), tf.float32))

    tf.compat.v1.summary.scalar('loss', loss)
    return end_points, loss, accuracy

def learning_rate_scheduler(Learning_rate, epoch, num_epoch):
    Learning_rate = tf.case([
                             (tf.less(epoch, num_epoch//2),    lambda : Learning_rate),
                             (tf.less(epoch,(num_epoch*3)//4), lambda : Learning_rate*1e-1),
                             (tf.less(epoch, num_epoch),       lambda : Learning_rate*1e-2),
                             ],
                             default =                         lambda : 0.0)
    tf.compat.v1.summary.scalar('learning_rate', Learning_rate)
    return Learning_rate

#%%    
with tf.Graph().as_default() as graph:
    ## Load Dataset
    train_image = tf.keras.backend.placeholder(shape = [None]+image_size, dtype = tf.float32)
    train_label = tf.keras.backend.placeholder(shape = [None], dtype = tf.int32)
    global_step = tf.keras.backend.placeholder(dtype = tf.int32)
    is_training = tf.keras.backend.placeholder(dtype = tf.uint8)

    train_label_onhot = tf.one_hot(train_label, num_labels, on_value=1.0)
 
    decay_steps = dataset_len // batch_size
    epoch = tf.floor_div(tf.cast(global_step, tf.float32), decay_steps)
    max_number_of_steps = int(decay_steps*num_epoch)
    
    LR = learning_rate_scheduler(Learning_rate, epoch, num_epoch) 
    
    ## load Net
    train_end_points, total_loss, train_accuracy = MODEL(model_name, weight_decay, train_image,
                                                         train_label_onhot, True, is_training)

    #get variables and make optimizer 
    variables  = graph.get_collection('trainable_variables')
    update_ops = graph.get_collection('update_ops')
    optimizer = tf.keras.optimizers.SGD(LR, 0.9, nesterov=True)

    total_loss = tf.add_n([total_loss]
                         +[reg*weight_decay for reg in graph.get_collection('reg')]
                         )
    gradients = optimizer.get_gradients(total_loss, variables)
    grads_and_vars = [(g, v) for g, v in zip(gradients, variables)]
    update_ops.append(optimizer.apply_gradients(grads_and_vars))
    update_op = tf.group(*update_ops)
    train_op = control_flow_ops.with_dependencies([update_op], total_loss, name='train_op')

    ## collect summary ops for plotting in tensorboard
    summaries = set(graph.get_collection('summaries'))
    summary_op = tf.compat.v1.summary.merge(list(summaries), name='summary_op')   
    
    ## start training
    step = 0 ; highest = 0
    train_writer = tf.compat.v1.summary.FileWriter('%s'%train_dir,graph,flush_secs=save_summaries_secs)
    val_saver   = tf.compat.v1.train.Saver()

    train_labels = dataset['train_label']
    train_images = dataset['train_image']
    
    val_labels   = dataset['val_label'].T
    val_images   = dataset['val_image']

    val_itr = val_images.shape[0]//val_batch_size
    
    train_acc_place = tf.keras.backend.placeholder(dtype=tf.float32)
    val_acc_place   = tf.keras.backend.placeholder(dtype=tf.float32)
    val_summary = [
                   tf.compat.v1.summary.scalar('accuracy/training_accuracy', train_acc_place),
                   tf.compat.v1.summary.scalar('accuracy/validation_accuracy', val_acc_place),
                   ]
    
    val_summary_op = tf.compat.v1.summary.merge(list(val_summary), name='val_summary_op') 
    
    def pre_processing(image):
        random_seed = [1]*(batch_size//2) + [-1]*(batch_size//2)
        shuffle(random_seed)
        image = [ti if seed > 0 else np.fliplr(ti)
                 for seed, ti in zip(random_seed, image)]
        image = np.pad(image, [[0,0],[4,4],[4,4],[0,0]], 'constant')
        
        def random_crop(img):
            yi, xi = np.random.randint(0,8,2)
            return img[yi:yi+32, xi:xi+32]
        
        image = [random_crop(img) for img in image]
        return image
    
    with tf.compat.v1.Session(graph = graph) as sess:
        ## ready for training , init variables create coordinator etc.
        sess.run([v.initializer for v in set(graph.get_collection('variables')) if hasattr(v, 'initializer')])
        ## training loop
        sum_train_accuracy = 0
        time_elapsed = 0
        total_loss = 0

        idx = list(range(train_labels.shape[0]))
        shuffle(idx)
        for step in range(max_number_of_steps):
            start_time = time.time()
            train_batch = train_images[idx[:batch_size]]
            tl, log, train_acc = sess.run([train_op, summary_op, train_accuracy],
                                          feed_dict = {train_image : pre_processing(train_batch),
                                                       train_label : np.squeeze(train_labels[idx[:batch_size]]),
                                                       global_step : step,
                                                       is_training : 1})
            time_elapsed += time.time() - start_time
            idx[:batch_size] = []
            if len(idx) < batch_size:
                idx += list(range(train_labels.shape[0]))
                shuffle(idx)

            total_loss += tl
            sum_train_accuracy += train_acc
            if ( step % (decay_steps) == 0)|( step == max_number_of_steps-1):
                ## test in each end of epoch
                sum_val_accuracy = 0
                
                for i in range(val_itr):
                    val_batch = val_images[i*val_batch_size:(i+1)*val_batch_size]
                    acc = sess.run(train_accuracy, feed_dict = {train_image : val_batch,
                                                                train_label : np.squeeze(val_labels[i*val_batch_size:(i+1)*val_batch_size]),
                                                                is_training : 0})
                    sum_val_accuracy += acc

                print ('Epoch %s Step %s - train_Accuracy : %.2f%%  val_Accuracy : %.2f%%'
                                %(str((step)//decay_steps).rjust(3, '0'), str(step).rjust(6, '0'), 
                                sum_train_accuracy *100/decay_steps, sum_val_accuracy *100/val_itr))

                result_log = sess.run(val_summary_op, feed_dict={
                                                                 train_acc_place : sum_train_accuracy*100/decay_steps,
                                                                 val_acc_place   : sum_val_accuracy*100/val_itr,
                                                                 })
                if step == max_number_of_steps-1:
                    train_writer.add_summary(result_log, num_epoch)
                else:
                    train_writer.add_summary(result_log, (step)//decay_steps)
                var = {}
                variables  = graph.get_collection('trainable_variables')
                for v in variables:
                    var[v.name[:-2]] = sess.run(v)
                sio.savemat(train_dir + '/best_params.mat',var)

                val_saver.save(sess, "%s/best_model.ckpt"%train_dir)
                sum_train_accuracy = 0
                
            if (step % should_log == 0)&(step > 0):
                print ('global step %s: loss = %.4f (%.3f sec/step)'%(str(step).rjust(6, '0'), total_loss/should_log, time_elapsed/should_log))
                time_elapsed = 0
                total_loss = 0
            
            elif (step % (decay_steps//2) == 0):
                train_writer.add_summary(log, step)

        ## save variables to use for something
        var = {}
        variables  = graph.get_collection('trainable_variables')
        for v in variables:
            var[v.name[:-2]] = sess.run(v)
        sio.savemat(train_dir + '/train_params.mat',var)
        
        ## close all
        tf.logging.info('Finished training! Saving model to disk.')
        train_writer.add_session_log(tf.SessionLog(status=tf.SessionLog.STOP))
        train_writer.close()
        




