#!/usr/bin/env python

from __future__ import division
import os, sys
import math, random, time # used for the expression eval context

from rosgui.QtBindingHelper import loadUi
from QtCore import Qt, QTimer, QSignalMapper, Slot, qDebug, qWarning
from QtGui import QDockWidget, QTreeWidgetItem, QMenu

import roslib
roslib.load_manifest('rosgui_service_caller')
import rospy, rosservice
from ExtendedComboBox import ExtendedComboBox

# main class inherits from the ui window class
class ServiceCaller(QDockWidget):
    column_names = ['service', 'type', 'expression']


    def __init__(self, parent, plugin_context):
        super(ServiceCaller, self).__init__(plugin_context.main_window())
        self.setObjectName('ServiceCaller')

        # create context for the expression eval statement
        self._eval_locals = {}
        self._eval_locals.update(math.__dict__)
        self._eval_locals.update(random.__dict__)
        self._eval_locals.update(time.__dict__)
        del self._eval_locals['__name__']
        del self._eval_locals['__doc__']

        ui_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'ServiceCaller.ui')
        loadUi(ui_file, self, {'ExtendedComboBox': ExtendedComboBox})

        if plugin_context.serial_number() > 1:
            self.setWindowTitle(self.windowTitle() + (' (%d)' % plugin_context.serial_number()))

        self._column_index = {}
        for column_name in self.column_names:
            self._column_index[column_name] = len(self._column_index)

        self._service_info = None
        self.refresh_services()

        self.request_tree_widget.itemChanged.connect(self.request_tree_widget_itemChanged)

        # add our self to the main window
        plugin_context.main_window().addDockWidget(Qt.RightDockWidgetArea, self)


    def refresh_services(self):
        service_names = rosservice.get_service_list()
        self._services = {}
        for service_name in service_names:
            self._services[service_name] = rosservice.get_service_class_by_name(service_name)
            #qDebug('ServiceCaller.refresh_services(): found service %s using class %s' % (service_name, self._services[service_name]))

        self.service_combo_box.clear()
        self.service_combo_box.addItems(sorted(service_names))


    @Slot(str)
    def on_service_combo_box_currentIndexChanged(self, service_name):
        self.request_tree_widget.clear()
        self.response_tree_widget.clear()
        service_name = str(service_name)

        self._service_info = {}
        self._service_info['service_name'] = service_name
        self._service_info['service_class'] = self._services[service_name]
        self._service_info['service_proxy'] = rospy.ServiceProxy(service_name, self._service_info['service_class'])
        self._service_info['expressions'] = {}
        self._service_info['counter'] = 0

        # recursively create widget items for the service request's slots
        request_class = self._service_info['service_class']._request_class
        top_level_item = self._recursive_create_widget_items(None, service_name, request_class._type, request_class())

        # add top level item to tree widget
        self.request_tree_widget.addTopLevelItem(top_level_item)

        # resize columns
        self.request_tree_widget.expandAll()
        for i in range(self.request_tree_widget.columnCount()):
            self.request_tree_widget.resizeColumnToContents(i)


    def _recursive_create_widget_items(self, parent, topic_name, type_name, message, is_editable=True):
        item = QTreeWidgetItem(parent)
        if is_editable:
            item.setFlags(item.flags() | Qt.ItemIsEditable)
        else:
            item.setFlags(item.flags() & (~Qt.ItemIsEditable))

        if parent is None:
            # show full topic name with preceding namespace on toplevel item
            topic_text = topic_name
        else:
            topic_text = topic_name.split('/')[-1]

        item.setText(self._column_index['service'], topic_text)
        item.setText(self._column_index['type'], type_name)

        item.setData(0, Qt.UserRole, topic_name)

        if hasattr(message, '__slots__') and hasattr(message, '_slot_types'):
            for slot_name, type_name in zip(message.__slots__, message._slot_types):
                self._recursive_create_widget_items(item, topic_name + '/' + slot_name, type_name, getattr(message, slot_name), is_editable)

        elif type(message) in (list, tuple) and (len(message) > 0) and hasattr(message[0], '__slots__'):
            type_name = type_name.split('[', 1)[0]
            for index, slot in enumerate(message):
                self._recursive_create_widget_items(item, topic_name + '[%d]' % index, type_name, slot, is_editable)

        else:
            item.setText(self._column_index['expression'], repr(message))

        return item


    @Slot('QTreeWidgetItem*', int)
    def request_tree_widget_itemChanged(self, item, column):
        column_name = self.column_names[column]
        new_value = str(item.text(column))
        qDebug('ServiceCaller.request_tree_widget_itemChanged(): %s : %s' % (column_name, new_value))

        if column_name == 'expression':
            topic_name = str(item.data(0, Qt.UserRole))
            self._service_info['expressions'][topic_name] = new_value
            qDebug('ServiceCaller.request_tree_widget_itemChanged(): %s expression: %s' % (topic_name, new_value))


    def fill_message_slots(self, message, topic_name, expressions, counter):
        if not hasattr(message, '__slots__'):
            return
        for slot_name in message.__slots__:
            slot_key = topic_name + '/' + slot_name

            # if no expression exists for this slot_key, continue with it's child slots
            if slot_key not in expressions:
                self.fill_message_slots(getattr(message, slot_name), slot_key, expressions, counter)
                continue

            expression = expressions[slot_key]
            if len(expression) == 0:
                continue

            # get slot type
            slot = getattr(message, slot_name)
            if hasattr(slot, '_type'):
                slot_type = slot._type
            else:
                slot_type = type(slot)

            self._eval_locals['i'] = counter
            value = self._evaluate_expression(expression, slot_type)
            if value is not None:
                setattr(message, slot_name, value)


    def _evaluate_expression(self, expression, slot_type):
        successful_eval = True
        successful_conversion = True

        try:
            # try to evaluate expression
            value = eval(expression, {}, self._eval_locals)
        except Exception:
            # just use expression-string as value
            value = expression
            successful_eval = False

        try:
            # try to convert value to right type
            value = slot_type(value)
        except Exception:
            successful_conversion = False

        if successful_conversion:
            return value
        elif successful_eval:
            qWarning('ServiceCaller.fill_message_slots(): can not convert expression to slot type: %s -> %s' % (type(value), slot_type))
        else:
            qWarning('ServiceCaller.fill_message_slots(): failed to evaluate expression: %s' % (expression))

        return None


    @Slot()
    def on_call_service_button_clicked(self):
        self.response_tree_widget.clear()

        request = self._service_info['service_class']._request_class()
        self.fill_message_slots(request, self._service_info['service_name'], self._service_info['expressions'], self._service_info['counter'])
        try:
            response = self._service_info['service_proxy'](request)
        except rospy.ServiceException, e:
            qDebug('ServiceCaller.on_call_service_button_clicked(): request:\n%r' % (request))
            qDebug('ServiceCaller.on_call_service_button_clicked(): error calling service "%s":\n%s' % (self._service_info['service_name'], e))
            top_level_item = QTreeWidgetItem()
            top_level_item.setText(self._column_index['service'], 'ERROR')
            top_level_item.setText(self._column_index['type'], 'rospy.ServiceException')
            top_level_item.setText(self._column_index['expression'], str(e))
        else:
            #qDebug('ServiceCaller.on_call_service_button_clicked(): response: %r' % (response))
            top_level_item = self._recursive_create_widget_items(None, '/', response._type, response, is_editable=False)

        self.response_tree_widget.addTopLevelItem(top_level_item)
        # resize columns
        self.response_tree_widget.expandAll()
        for i in range(self.response_tree_widget.columnCount()):
            self.response_tree_widget.resizeColumnToContents(i)


    def save_settings(self, global_settings, perspective_settings):
        pass


    def restore_settings(self, global_settings, perspective_settings):
        pass


    def set_name(self, name):
        self.setWindowTitle(name)


    # override Qt's closeEvent() method to trigger plugin unloading
    def closeEvent(self, event):
        event.ignore()
        self.deleteLater()


    def close_plugin(self):
        QDockWidget.close(self)